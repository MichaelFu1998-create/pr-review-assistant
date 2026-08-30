"""Adaptive mode: recon, rule authoring, then the standard review."""

import subprocess

import pytest

import src.agent.adaptive as adaptive_module
from src.agent.adaptive import run_adaptive_agent
from src.agent.budget import Budget
from src.agent.context import PRMetadata, ReviewContext
from src.agent.findings import FindingCollector
from src.agent.rules import RuleCollector
from src.agent.toolbelt import Toolbelt
from src.config import Config
from src.diff.patch import DiffMap
from src.llm.base import LLMConfig, ToolCall
from tests.fakes import FakeProvider, call, turn

PATCH = "@@ -1,3 +1,5 @@\n import os\n+@bp.route('/x')\n+def handler(): pass\n def f():\n"

RULE = """
rules:
  - id: route-missing-auth
    languages: [python]
    severity: ERROR
    message: Route has no @requires_scope
    pattern: |
      @bp.route(...)
      def $F(...): ...
"""


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "app.py").write_text(
        "import os\n@bp.route('/x')\ndef handler(): pass\ndef f():\n    pass\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"],
        cwd=tmp_path, check=True,
    )
    return str(tmp_path)


@pytest.fixture
def context(workspace):
    return ReviewContext(
        workspace=workspace,
        diff=DiffMap.from_pull_files({"app.py": {"patch": PATCH, "status": "modified"}}),
        metadata=PRMetadata(title="Add a route"),
    )


def script(rule_yaml=RULE, extra_rule=None):
    """recon -> author -> review, the three phases in order."""
    author_turns = [
        turn(call("write_rule", _id="w1", rule_yaml=rule_yaml,
                  rationale="every other route in app/ carries @requires_scope"))
    ]
    if extra_rule is not None:
        author_turns.append(
            turn(call("write_rule", _id="w2", rule_yaml=extra_rule, rationale="second"))
        )
    return [
        # phase 1: recon
        turn(call("list_changed_files", _id="r1")),
        turn(call("finish", _id="r2",
                  summary="Flask app; every route carries @requires_scope.")),
        # phase 2: authoring
        *author_turns,
        turn(call("finish", _id="w9", summary="Wrote one rule for the auth decorator.")),
        # phase 3: review
        turn(call("post_finding", _id="p1", path="app.py", line=2, severity="high",
                  category="security", title="Route missing auth", body="b",
                  confidence="high")),
        turn(call("finish", _id="p2", summary="One issue.")),
    ]


def run(context, turns, config=None, monkeypatch=None):
    llm = FakeProvider(turns)
    config = config or Config(agent_mode="adaptive", max_custom_rules=10)
    result = run_adaptive_agent(
        llm, LLMConfig(), config, context,
        Budget(max_steps=40, max_tokens=400_000),
    )
    return result, llm


class TestPhases:
    def test_all_three_phases_run_in_order(self, context, monkeypatch):
        monkeypatch.setattr(adaptive_module, "_run_custom_rules", lambda *a: None)
        result, llm = run(context, script())
        prompts = [c["system"] for c in llm.calls]
        assert "reconnaissance" in prompts[0]
        assert any("semgrep rules" in p for p in prompts)
        assert any("professional code reviewer" in p or "reviewing a pull request" in p
                   for p in prompts)

    def test_recon_brief_is_carried_into_authoring(self, context, monkeypatch):
        monkeypatch.setattr(adaptive_module, "_run_custom_rules", lambda *a: None)
        _, llm = run(context, script())
        authoring_kickoff = next(
            c["messages"][0].content for c in llm.calls
            if "semgrep rules" in c["system"]
        )
        assert "@requires_scope" in authoring_kickoff

    def test_recon_is_told_not_to_report(self, context, monkeypatch):
        monkeypatch.setattr(adaptive_module, "_run_custom_rules", lambda *a: None)
        _, llm = run(context, script())
        assert "Do not review anything yet" in llm.calls[0]["system"]

    def test_result_carries_rules_and_brief(self, context, monkeypatch):
        monkeypatch.setattr(adaptive_module, "_run_custom_rules", lambda *a: None)
        result, _ = run(context, script())
        assert result.recon_brief.startswith("Flask app")
        assert [r.rule_id for r in result.custom_rules if r.valid] == ["route-missing-auth"]

    def test_review_findings_still_produced(self, context, monkeypatch):
        monkeypatch.setattr(adaptive_module, "_run_custom_rules", lambda *a: None)
        result, _ = run(context, script())
        assert [f.title for f in result.findings] == ["Route missing auth"]


class TestRuleAuthoringTool:
    def _belt(self, context, max_rules=10):
        return Toolbelt(
            context, FindingCollector(), Budget(),
            rule_collector=RuleCollector(max_rules=max_rules),
        )

    def test_write_rule_absent_without_a_collector(self, context):
        belt = Toolbelt(context, FindingCollector(), Budget())
        assert "write_rule" not in [s.name for s in belt.schemas()]

    def test_write_rule_offered_during_authoring(self, context):
        assert "write_rule" in [s.name for s in self._belt(context).schemas()]

    def test_accepted_rule_confirms_with_slots_left(self, context):
        belt = self._belt(context)
        out = belt.dispatch(ToolCall(id="c", name="write_rule", arguments={
            "rule_yaml": RULE, "rationale": "why"})).text
        assert "accepted" in out and "slot(s) left" in out
        assert belt.rule_collector.accepted[0].rationale == "why"

    def test_rejected_rule_is_fed_back_for_retry(self, context):
        belt = self._belt(context)
        bad = RULE + "\n    pattern-where-python: \"True\"\n"
        out = belt.dispatch(ToolCall(id="c", name="write_rule", arguments={
            "rule_yaml": bad, "rationale": "why"})).text
        assert "Rule rejected" in out and "executes Python" in out
        assert belt.rule_collector.accepted == []

    def test_cap_is_enforced_through_the_tool(self, context):
        belt = self._belt(context, max_rules=1)
        for i in range(2):
            text = RULE.replace("route-missing-auth", f"rule-{i}")
            out = belt.dispatch(ToolCall(id="c", name="write_rule", arguments={
                "rule_yaml": text, "rationale": "r"})).text
        assert "limit" in out
        assert len(belt.rule_collector.accepted) == 1


class TestCustomRuleExecution:
    def test_semgrep_is_never_invoked_with_a_dangerous_flag(self, context, monkeypatch):
        """Assert on the argv we actually build, not on source text — a comment
        mentioning the flag must not make this test pass or fail."""
        import src.agent.rules as rules_module
        import src.tools.analyzers.semgrep as semgrep_module

        argvs = []

        class Result:
            returncode = 0
            stdout = '{"results": [], "errors": []}'
            stderr = ""

        def capture(cmd, *a, **k):
            argvs.append(cmd)
            return Result()

        monkeypatch.setattr(rules_module.subprocess, "run", capture)
        monkeypatch.setattr(semgrep_module.subprocess, "run", capture)
        monkeypatch.setattr(semgrep_module.SemgrepTool, "is_available", lambda self: True)

        # validation path and execution path both invoke semgrep
        rules_module.validate_rule(RULE, RuleCollector(), run_semgrep_validate=True)
        run(context, script())

        assert argvs, "expected semgrep to be invoked"
        for cmd in argvs:
            assert cmd[0] == "semgrep"
            for arg in cmd:
                assert not str(arg).startswith("--dangerously"), cmd

    def test_custom_rules_run_with_only_a_ruleset_config(self, context, monkeypatch):
        seen = {}

        def capture(tool, files, workspace, config):
            from src.tools.base import ToolResult
            seen["config"] = config
            return ToolResult(tool_name="semgrep", findings=[])

        monkeypatch.setattr(adaptive_module, "_run_single_tool", capture)
        run(context, script())
        assert list(seen["config"]) == ["rulesets"]
        assert seen["config"]["rulesets"][0].endswith("adaptive-rules.yaml")

    def test_hits_are_tagged_and_counted(self, context, monkeypatch):
        from src.tools.base import Finding, ToolResult

        def fake(tool, files, workspace, config):
            return ToolResult(tool_name="semgrep", findings=[
                Finding(file="app.py", line=2, severity="high", category="security",
                        rule_id="route-missing-auth", message="no auth", tool="semgrep")
            ])

        monkeypatch.setattr(adaptive_module, "_run_single_tool", fake)
        result, _ = run(context, script())

        assert context.tool_findings[0].tool == "custom-rule"
        assert [r.hits for r in result.custom_rules if r.valid] == [1]

    def test_no_rules_means_semgrep_is_not_run(self, context, monkeypatch):
        called = {"n": 0}

        def counter(*a, **k):
            called["n"] += 1

        monkeypatch.setattr(adaptive_module, "_run_single_tool", counter)
        # authoring writes only an invalid rule
        run(context, script(rule_yaml="id: [broken"))
        assert called["n"] == 0


class TestDegradedRuns:
    def test_empty_recon_brief_skips_authoring(self, context, monkeypatch):
        monkeypatch.setattr(adaptive_module, "_run_custom_rules", lambda *a: None)
        turns = [
            turn(call("finish", _id="r1", summary="")),   # recon says nothing
            turn(call("post_finding", _id="p1", path="app.py", line=2, severity="low",
                      category="design", title="Nit", body="b")),
            turn(call("finish", _id="p2", summary="done")),
        ]
        result, _ = run(context, turns)
        assert result.custom_rules == []
        assert [f.title for f in result.findings] == ["Nit"]

    def test_budget_accumulates_across_phases(self, context, monkeypatch):
        monkeypatch.setattr(adaptive_module, "_run_custom_rules", lambda *a: None)
        result, _ = run(context, script())
        # recon 2 + authoring 2 + review 2
        assert result.budget["steps"] >= 6
        assert result.budget["total_tokens"] > 0
