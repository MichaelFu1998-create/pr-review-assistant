"""Toolbelt tests, run against a real temporary git checkout."""

import subprocess

import pytest

from src.agent.budget import Budget
from src.agent.context import PRMetadata, ReviewContext
from src.agent.findings import FindingCollector
from src.agent.toolbelt import Toolbelt, _escape_regex
from src.diff.patch import DiffMap
from src.llm.base import ToolCall
from src.tools.base import Finding

PATCH = """@@ -1,3 +1,5 @@
 import os
+import pickle
+
 def load(raw):
     return raw
"""


@pytest.fixture
def workspace(tmp_path):
    """A real git repo, since search and history shell out to git."""
    (tmp_path / "a.py").write_text(
        "import os\nimport pickle\n\ndef load(raw):\n    return pickle.loads(raw)\n"
    )
    (tmp_path / "helper.py").write_text("def load(raw):\n    return raw\n")
    (tmp_path / "secret.txt").write_text("nothing here\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=tmp_path,
        check=True,
    )
    return str(tmp_path)


@pytest.fixture
def belt(workspace):
    context = ReviewContext(
        workspace=workspace,
        diff=DiffMap.from_pull_files({"a.py": {"patch": PATCH, "status": "modified"}}),
        metadata=PRMetadata(
            title="Add loader",
            description="Adds a loader.",
            author="student",
            comments=["Does this handle bad input?"],
            labels=["enhancement"],
        ),
        tool_findings=[
            Finding(
                file="a.py",
                line=5,
                severity="high",
                category="security",
                rule_id="B301",
                message="pickle.loads on untrusted input",
                tool="bandit",
            )
        ],
        tools_used=["bandit"],
    )
    return Toolbelt(context, FindingCollector(), Budget())


def run(belt, tool, /, **arguments):
    return belt.dispatch(ToolCall(id="c1", name=tool, arguments=arguments))


class TestDispatch:
    def test_unknown_tool_lists_the_real_ones(self, belt):
        out = run(belt, "delete_everything").text
        assert "unknown tool" in out and "post_finding" in out

    def test_parse_error_is_reported_back_for_retry(self, belt):
        outcome = belt.dispatch(
            ToolCall(id="c1", name="read_diff", parse_error="Expecting ',' delimiter")
        )
        assert "not valid JSON" in outcome.text

    def test_handler_exception_does_not_end_the_review(self, belt, monkeypatch):
        def boom(args):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(belt, "_tool_read_diff", boom)
        assert "kaboom" in run(belt, "read_diff", path="a.py").text


class TestReadTools:
    def test_list_changed_files_includes_stats_and_tool_hits(self, belt):
        out = run(belt, "list_changed_files").text
        assert "a.py" in out and "+2/-0" in out
        assert "1 tool finding(s)" in out

    def test_read_diff_is_line_numbered(self, belt):
        out = run(belt, "read_diff", path="a.py").text
        assert "@@ -1,3 +1,5 @@" in out
        assert "     2 + import pickle" in out

    def test_read_diff_unknown_path(self, belt):
        assert "No diff" in run(belt, "read_diff", path="zzz.py").text

    def test_read_file_numbers_lines(self, belt):
        out = run(belt, "read_file", path="a.py").text
        assert "a.py (lines 1-5 of 5)" in out
        assert "     2 | import pickle" in out

    def test_read_file_slice(self, belt):
        out = run(belt, "read_file", path="a.py", start_line=2, end_line=3).text
        assert "lines 2-3 of 5" in out
        assert "import os" not in out

    def test_read_file_missing(self, belt):
        assert "does not exist" in run(belt, "read_file", path="nope.py").text

    def test_read_pr_metadata(self, belt):
        out = run(belt, "read_pr_metadata").text
        assert "Add loader" in out and "student" in out
        assert "enhancement" in out
        assert "Does this handle bad input?" in out


class TestPathEscape:
    """Paths come from model output and file contents; both are untrusted."""

    @pytest.mark.parametrize(
        "path", ["../../../etc/passwd", "/etc/passwd", "a/../../../../etc/hosts"]
    )
    def test_read_file_refuses_to_escape_the_workspace(self, belt, path):
        assert "outside the repository" in run(belt, "read_file", path=path).text

    def test_git_log_refuses_to_escape(self, belt):
        assert "outside the repository" in run(belt, "git_log", path="../..").text

    def test_run_analyzer_refuses_to_escape(self, belt):
        out = run(belt, "run_analyzer", tool="ruff", paths=["../../etc/passwd"]).text
        assert "no valid paths" in out

    def test_resolve_allows_normal_paths(self, belt):
        assert belt.context.resolve("a.py") is not None
        assert belt.context.resolve("./a.py") is not None


class TestSearch:
    def test_search_finds_matches_with_line_numbers(self, belt):
        out = run(belt, "search_repo", pattern="pickle").text
        assert "a.py:2" in out

    def test_search_no_matches(self, belt):
        assert "No matches" in run(belt, "search_repo", pattern="zzzz").text

    def test_search_requires_a_pattern(self, belt):
        assert "required" in run(belt, "search_repo", pattern="").text

    def test_search_glob_filter(self, belt):
        out = run(belt, "search_repo", pattern="load", glob="helper.py").text
        assert "helper.py" in out and "a.py:" not in out

    def test_find_symbol_separates_definitions_from_references(self, belt):
        out = run(belt, "find_symbol", name="load").text
        assert "## Definitions" in out and "## References" in out
        assert "def load" in out

    def test_find_symbol_unknown(self, belt):
        out = run(belt, "find_symbol", name="nonexistent_symbol_xyz").text
        assert "(none found)" in out

    def test_find_symbol_requires_a_name(self, belt):
        assert "required" in run(belt, "find_symbol", name="").text

    def test_regex_metacharacters_are_escaped(self):
        assert _escape_regex("a.b(c)") == r"a\.b\(c\)"


class TestGitLog:
    def test_returns_history(self, belt):
        out = run(belt, "git_log", path="a.py").text
        assert "init" in out


class TestAnalyzers:
    def test_list_analyzers_names_the_pre_pass(self, belt):
        out = run(belt, "list_analyzers").text
        assert "Already run in the pre-pass: bandit" in out
        assert "semgrep" in out

    def test_run_analyzer_rejects_unknown_tool(self, belt):
        out = run(belt, "run_analyzer", tool="nosuchtool", paths=["a.py"]).text
        assert "unknown analyser" in out

    def test_run_analyzer_reports_language_mismatch(self, belt):
        out = run(belt, "run_analyzer", tool="ruff", paths=["secret.txt"]).text
        assert "does not handle" in out


class TestPostFindingAndFinish:
    def test_post_finding_records_and_confirms(self, belt):
        out = run(
            belt,
            "post_finding",
            path="a.py",
            line=5,
            severity="critical",
            category="security",
            cwe="CWE-502",
            title="Unsafe deserialization",
            body="pickle.loads on untrusted input.",
            confidence="high",
        ).text
        assert "Recorded" in out
        finding = belt.collector.findings[0]
        assert finding.cwe == "CWE-502" and finding.severity == "critical"

    def test_post_finding_validates(self, belt):
        assert "path" in run(belt, "post_finding", title="x").text
        assert len(belt.collector) == 0

    def test_finding_on_an_unchanged_file_is_allowed(self, belt):
        """A change can break a caller the PR never touched."""
        out = run(
            belt,
            "post_finding",
            path="helper.py",
            severity="medium",
            category="correctness",
            title="Caller not updated",
            body="b",
        ).text
        assert "Recorded" in out

    def test_finish_signals_termination_with_payload(self, belt):
        outcome = run(belt, "finish", summary="All good", scores={"security": 4})
        assert outcome.is_finish
        assert outcome.payload == {"summary": "All good", "scores": {"security": 4}}

    def test_finish_tolerates_a_non_dict_scores(self, belt):
        outcome = run(belt, "finish", summary="s", scores="great")
        assert outcome.payload["scores"] == {}


class TestSchemas:
    def test_every_schema_has_a_handler(self, belt):
        for schema in belt.schemas():
            assert hasattr(belt, f"_tool_{schema.name}"), schema.name

    def test_schemas_are_well_formed(self, belt):
        for schema in belt.schemas():
            assert schema.description.strip()
            assert schema.parameters["type"] == "object"
            for required in schema.parameters.get("required", []):
                assert required in schema.parameters["properties"], schema.name
