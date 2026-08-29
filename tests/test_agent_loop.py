"""Tests for the agent loop and its budget guards, driven by a scripted model."""


from src.agent.budget import Budget
from src.agent.context import PRMetadata, ReviewContext
from src.agent.findings import FindingCollector
from src.agent.loop import run_agent
from src.agent.toolbelt import Toolbelt
from src.diff.patch import DiffMap
from src.llm.base import LLMConfig, Usage
from tests.fakes import FakeProvider, call, turn

PATCH = """@@ -1,3 +1,4 @@
 import os
+import pickle
 
 def load(raw):
"""


def _harness(script, budget=None, workspace="."):
    context = ReviewContext(
        workspace=workspace,
        diff=DiffMap.from_pull_files({"a.py": {"patch": PATCH, "status": "modified"}}),
        metadata=PRMetadata(title="Add loader", description="Loads things."),
    )
    collector = FindingCollector()
    budget = budget or Budget()
    toolbelt = Toolbelt(context, collector, budget)
    llm = FakeProvider(script)
    return llm, toolbelt, collector, budget


def _run(script, budget=None):
    llm, toolbelt, collector, budget = _harness(script, budget)
    result = run_agent(
        llm=llm,
        llm_config=LLMConfig(),
        system_prompt="sys",
        kickoff="review this",
        toolbelt=toolbelt,
        collector=collector,
        budget=budget,
    )
    return result, llm


class TestHappyPath:
    def test_investigate_report_finish(self):
        result, llm = _run(
            [
                turn(call("read_diff", path="a.py")),
                turn(
                    call(
                        "post_finding",
                        path="a.py",
                        line=2,
                        severity="high",
                        category="security",
                        cwe="CWE-502",
                        title="Unsafe deserialization",
                        body="pickle on untrusted input.",
                    )
                ),
                turn(call("finish", summary="Looks risky.", scores={"security": 2})),
            ]
        )
        assert result.stopped_because == "finished"
        assert result.summary == "Looks risky."
        assert result.scores == {"security": 2}
        assert len(result.findings) == 1
        assert result.findings[0].cwe == "CWE-502"
        assert result.budget["steps"] == 3

    def test_parallel_tool_calls_in_one_turn(self):
        result, _ = _run(
            [
                turn(
                    call("read_diff", _id="c1", path="a.py"),
                    call("list_changed_files", _id="c2"),
                ),
                turn(call("finish", summary="done")),
            ]
        )
        assert result.stopped_because == "finished"
        assert result.budget["steps"] == 2

    def test_tool_results_are_fed_back_to_the_model(self):
        _, llm = _run(
            [turn(call("read_diff", path="a.py")), turn(call("finish", summary="d"))]
        )
        second_turn_messages = llm.calls[1]["messages"]
        roles = [m.role for m in second_turn_messages]
        assert roles == ["user", "assistant", "tool"]
        assert "import pickle" in second_turn_messages[-1].content

    def test_all_tools_are_offered(self):
        _, llm = _run([turn(call("finish", summary="d"))])
        offered = llm.calls[0]["tool_names"]
        assert "post_finding" in offered and "finish" in offered
        assert "read_diff" in offered and "run_analyzer" in offered


class TestTermination:
    def test_model_that_stops_talking_ends_the_loop(self):
        result, _ = _run([turn(text="I think this is fine.")])
        assert result.stopped_because == "model stopped without calling finish"
        assert result.summary == "I think this is fine."

    def test_step_limit_stops_the_loop(self):
        script = [turn(call("read_diff", path="a.py")) for _ in range(20)]
        result, _ = _run(script, budget=Budget(max_steps=3))
        assert "step limit" in result.stopped_because
        assert result.budget["steps"] <= 4  # + the closing summary turn

    def test_token_budget_stops_the_loop(self):
        script = [turn(call("read_diff", path="a.py"), tokens=1000) for _ in range(20)]
        result, _ = _run(script, budget=Budget(max_tokens=2500))
        assert "token budget" in result.stopped_because

    def test_repeated_identical_calls_are_refused(self):
        """A model looping on one call is stuck; tell it so rather than complying."""
        script = [turn(call("read_diff", path="a.py")) for _ in range(10)]
        script.append(turn(call("finish", summary="ok")))
        result, llm = _run(script, budget=Budget(max_steps=12))

        refusals = [
            m
            for turn_calls in llm.calls
            for m in turn_calls["messages"]
            if m.role == "tool" and "repeatedly" in m.content
        ]
        assert refusals, "expected the loop to push back on the repeat"

    def test_llm_error_ends_the_loop_without_losing_findings(self):
        llm, toolbelt, collector, budget = _harness([])
        llm.raise_on_call = RuntimeError("rate limited")
        result = run_agent(
            llm=llm,
            llm_config=LLMConfig(),
            system_prompt="sys",
            kickoff="go",
            toolbelt=toolbelt,
            collector=collector,
            budget=budget,
        )
        assert "rate limited" in result.stopped_because
        assert result.findings == []

    def test_findings_survive_a_budget_cutoff(self):
        script = [
            turn(
                call(
                    "post_finding",
                    path="a.py",
                    line=2,
                    severity="high",
                    category="security",
                    title="Found before cutoff",
                    body="b",
                )
            )
        ]
        script += [turn(call("read_file", path="a.py")) for _ in range(10)]
        result, _ = _run(script, budget=Budget(max_steps=3))
        assert [f.title for f in result.findings] == ["Found before cutoff"]

    def test_closing_summary_requested_without_tools(self):
        script = [turn(call("read_diff", path="a.py")) for _ in range(5)]
        script.append(turn(text="Cut short, but here is what I saw."))
        _, llm = _run(script, budget=Budget(max_steps=2))
        # The final call is the summary request: no tools offered.
        assert llm.calls[-1]["tool_names"] == []
        assert "budget" in llm.calls[-1]["messages"][-1].content


class TestBudget:
    def test_records_steps_and_usage(self):
        b = Budget()
        b.record_step(Usage(prompt_tokens=10, completion_tokens=5))
        b.record_step(Usage(prompt_tokens=1, completion_tokens=1))
        assert b.steps == 2 and b.tokens_used == 17

    def test_repeat_detection_allows_then_blocks(self):
        b = Budget(max_repeated_calls=2)
        assert b.record_call("read_file", {"path": "a"}) is True
        assert b.record_call("read_file", {"path": "a"}) is True
        assert b.record_call("read_file", {"path": "a"}) is False
        # different arguments are a different call
        assert b.record_call("read_file", {"path": "b"}) is True

    def test_summary_reports_completed_when_untripped(self):
        assert Budget().summary()["stop_reason"] == "completed"

    def test_remaining_steps(self):
        b = Budget(max_steps=5)
        b.record_step(Usage())
        assert b.remaining_steps() == 4
