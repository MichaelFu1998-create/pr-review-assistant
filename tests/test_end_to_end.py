"""End-to-end runs of the v2 path against a synthetic repository.

Drives src.main.run_agent_review with a scripted model and a mocked GitHub, so
the whole chain — pre-pass, agent loop, anchoring, SARIF, JSON, gating — is
exercised without a network call or an API key.
"""

import json
import subprocess
from unittest.mock import MagicMock

import pytest

import src.main as main_module
from src.config import Config
from src.llm.base import LLMConfig
from tests.fakes import FakeProvider, call, turn

VULNERABLE = '''import os
import pickle
import sqlite3


def get_user(conn, user_id):
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id = " + user_id)
    return cur.fetchone()


def load_session(raw):
    return pickle.loads(raw)


API_KEY = "sk-live-abcdef1234567890"


def run(cmd):
    os.system(cmd)
'''

PATCH = """@@ -0,0 +1,20 @@
+import os
+import pickle
+import sqlite3
+
+
+def get_user(conn, user_id):
+    cur = conn.cursor()
+    cur.execute("SELECT * FROM users WHERE id = " + user_id)
+    return cur.fetchone()
+
+
+def load_session(raw):
+    return pickle.loads(raw)
+
+
+API_KEY = "sk-live-abcdef1234567890"
+
+
+def run(cmd):
+    os.system(cmd)
"""


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "app.py").write_text(VULNERABLE)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=tmp_path,
        check=True,
    )
    return tmp_path


@pytest.fixture
def harness(repo, monkeypatch):
    monkeypatch.setenv("GITHUB_WORKSPACE", str(repo))
    # The pre-pass shells out to real analysers that are not installed here.
    monkeypatch.setattr(main_module, "run_prepass", lambda *a, **k: ([], []))
    monkeypatch.setattr(
        main_module,
        "fetch_pr_metadata",
        lambda pull: {
            "title": "Add user lookup",
            "description": "Adds a user lookup helper.",
            "author": "student",
            "comments": [],
            "labels": [],
            "base_ref": "main",
            "head_sha": "abc123",
        },
    )
    for name in ("check_pr_quality", "analyze_test_coverage", "check_git_hygiene"):
        monkeypatch.setattr(main_module, name, lambda *a, **k: [])

    posted = {}

    def fake_create_review(pull, body, comments):
        posted["body"] = body
        posted["comments"] = comments

    monkeypatch.setattr(main_module, "safe_create_review", fake_create_review)
    return repo, posted


FILES = {"app.py": {"sha": "abc123", "filename": "app.py", "patch": PATCH, "status": "added"}}


def _script():
    """A realistic agent run: look, investigate, report three issues, finish."""
    return [
        turn(call("list_changed_files", _id="c0")),
        turn(call("read_diff", _id="c1", path="app.py")),
        turn(
            call(
                "post_finding", _id="c2",
                path="app.py", line=8, severity="critical", category="security",
                cwe="CWE-89", title="SQL injection in get_user",
                body="user_id is concatenated into the query. Use a parameterised query.",
                confidence="high", evidence=["app.py:8"],
                suggested_fix='cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))',
            )
        ),
        turn(
            call(
                "post_finding", _id="c3",
                path="app.py", line=13, severity="high", category="security",
                cwe="CWE-502", title="Unsafe deserialization",
                body="pickle.loads on untrusted input allows code execution.",
                confidence="high",
            )
        ),
        turn(
            call(
                "post_finding", _id="c4",
                path="app.py", line=16, severity="critical", category="security",
                cwe="CWE-798", title="Hardcoded API key",
                body="A live-looking key is committed. Rotate it and read from the environment.",
                confidence="high",
            )
        ),
        turn(call("finish", _id="c5", summary="Three serious security issues.",
                  scores={"security": 1, "code_quality": 3})),
    ]


def _run(harness, config=None, script=None):
    repo, posted = harness
    config = config or Config(agent_mode="single", github_pr_id=42)
    llm = FakeProvider(script or _script())
    main_module.run_agent_review(
        config, llm, LLMConfig(), MagicMock(), MagicMock(), FILES, "owner/repo"
    )
    return posted, llm


class TestFullRun:
    def test_findings_become_anchored_inline_comments(self, harness):
        posted, _ = _run(harness)
        assert len(posted["comments"]) == 3
        lines = sorted(c["line"] for c in posted["comments"])
        assert lines == [8, 13, 16]
        for comment in posted["comments"]:
            assert comment["side"] == "RIGHT"
            assert comment["path"] == "app.py"
            assert "position" not in comment

    def test_review_body_summarises(self, harness):
        posted, _ = _run(harness)
        body = posted["body"]
        assert "Three serious security issues." in body
        assert "Critical" in body and "High" in body
        assert "security: 3" in body
        assert "mode: `single`" in body

    def test_cwe_links_appear_in_comments(self, harness):
        posted, _ = _run(harness)
        joined = " ".join(c["body"] for c in posted["comments"])
        assert "definitions/89.html" in joined
        assert "Suggested fix" in joined

    def test_agent_saw_the_diff_not_the_whole_file(self, harness):
        """The v1 failure this fixes: the model reviewed whole files."""
        _, llm = _run(harness)
        tool_outputs = [
            m.content
            for turn_ in llm.calls
            for m in turn_["messages"]
            if m.role == "tool"
        ]
        diff_output = next(o for o in tool_outputs if "@@" in o)
        assert "+ def get_user" in diff_output
        assert "@@ -0,0 +1,20 @@" in diff_output


class TestOutputArtifacts:
    def test_sarif_written_and_valid(self, harness, tmp_path):
        repo, _ = harness
        target = tmp_path / "out.sarif"
        _run(harness, Config(agent_mode="single", output_sarif=str(target)))

        doc = json.loads(target.read_text())
        assert doc["version"] == "2.1.0"
        results = doc["runs"][0]["results"]
        assert len(results) == 3
        assert {r["level"] for r in results} == {"error"}
        rules = doc["runs"][0]["tool"]["driver"]["rules"]
        assert {r["id"] for r in rules} == {"CWE-89", "CWE-502", "CWE-798"}
        for result in results:
            assert rules[result["ruleIndex"]]["id"] == result["ruleId"]

    def test_json_report_written(self, harness, tmp_path):
        target = tmp_path / "review.json"
        _run(
            harness,
            Config(agent_mode="single", output_json=str(target), github_pr_id=42),
        )
        report = json.loads(target.read_text())
        assert report["pr_number"] == 42
        assert report["repository"] == "owner/repo"
        assert report["totals"]["findings"] == 3
        assert report["totals"]["by_severity"] == {"critical": 2, "high": 1}
        assert report["scores"]["security"] == 1
        assert report["run"]["total_tokens"] > 0

    def test_no_artifacts_when_paths_are_empty(self, harness, tmp_path):
        _run(harness)
        assert list(tmp_path.glob("*.sarif")) == []


class TestGatingExit:
    def test_fail_on_exits_non_zero(self, harness):
        with pytest.raises(SystemExit) as exc:
            _run(harness, Config(agent_mode="single", fail_on="high"))
        assert exc.value.code == 1

    def test_below_threshold_does_not_exit(self, harness):
        script = [
            turn(
                call(
                    "post_finding",
                    path="app.py", line=8, severity="low", category="design",
                    title="Nit", body="b",
                )
            ),
            turn(call("finish", summary="minor only")),
        ]
        _run(harness, Config(agent_mode="single", fail_on="high"), script=script)

    def test_default_never_fails(self, harness):
        _run(harness, Config(agent_mode="single"))


class TestDegradedRuns:
    def test_llm_failure_still_posts_what_it_had(self, harness):
        repo, posted = harness

        class Flaky(FakeProvider):
            """Answers once, then goes down mid-review."""

            def complete_with_tools(self, *args, **kwargs):
                if self.calls:
                    raise RuntimeError("provider down")
                return super().complete_with_tools(*args, **kwargs)

        flaky = Flaky(
            [
                turn(
                    call(
                        "post_finding",
                        path="app.py", line=8, severity="high", category="security",
                        title="Found before the outage", body="b",
                    )
                )
            ]
        )
        main_module.run_agent_review(
            Config(agent_mode="single"), flaky, LLMConfig(),
            MagicMock(), MagicMock(), FILES, "owner/repo",
        )
        assert "Found before the outage" in " ".join(
            c["body"] for c in posted["comments"]
        )

    def test_empty_review_posts_nothing(self, harness):
        repo, posted = harness
        llm = FakeProvider([turn(call("finish", summary=""))])
        main_module.run_agent_review(
            Config(agent_mode="single"), llm, LLMConfig(),
            MagicMock(), MagicMock(), FILES, "owner/repo",
        )
        assert posted == {}
