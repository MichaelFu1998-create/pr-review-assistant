"""Path normalisation, source splitting, and the how-to-act guidance.

Regression cover for testbed#1, where analyser findings carried absolute paths
(/github/workspace/app/api.py) while the diff knew the file as app/api.py. They
never anchored and never deduplicated, so a review that posted 8 comments
claimed 27 findings.
"""

import os


from src.agent.findings import AgentFinding, merge_findings
from src.diff.patch import DiffMap
from src.output.comments import build_inline_comments, is_agent_finding, split_by_source
from src.output.summary import (
    build_review_body,
    format_analyser_table,
    format_how_to_act,
)
from src.tools.base import Finding
from src.tools.runner import relativize

PATCH = "@@ -1,2 +1,4 @@\n import os\n+import pickle\n+X = 1\n def f():\n"


def diff():
    return DiffMap.from_pull_files({"app/api.py": {"patch": PATCH, "status": "modified"}})


class TestRelativize:
    def test_absolute_path_under_the_workspace(self, tmp_path):
        ws = str(tmp_path)
        assert relativize(os.path.join(ws, "app/api.py"), ws) == "app/api.py"

    def test_the_actual_regression(self, tmp_path):
        """Exactly what bandit emitted on the testbed run."""
        ws = str(tmp_path / "github" / "workspace")
        os.makedirs(ws, exist_ok=True)
        assert relativize(f"{ws}/app/api.py", ws) == "app/api.py"

    def test_relative_path_untouched(self, tmp_path):
        assert relativize("app/api.py", str(tmp_path)) == "app/api.py"

    def test_dot_slash_prefix_stripped(self, tmp_path):
        assert relativize("./app/api.py", str(tmp_path)) == "app/api.py"
        assert relativize("././app/api.py", str(tmp_path)) == "app/api.py"

    def test_path_outside_the_workspace_left_alone(self, tmp_path):
        """Rewriting it would misattribute the finding to a file never scanned."""
        ws = str(tmp_path / "repo")
        os.makedirs(ws, exist_ok=True)
        assert relativize("/etc/passwd", ws) == "/etc/passwd"

    def test_workspace_root_itself(self, tmp_path):
        assert relativize(str(tmp_path), str(tmp_path)) == "."

    def test_empty_path(self, tmp_path):
        assert relativize("", str(tmp_path)) == ""


class TestDeduplicationAfterTheFix:
    def _tool_finding(self, path):
        return Finding(
            file=path, line=2, severity="high", category="security",
            rule_id="B403", message="pickle import", tool="bandit",
        )

    def test_matching_paths_now_deduplicate(self):
        agent = [AgentFinding(path="app/api.py", line=2, title="Unsafe pickle use")]
        merged = merge_findings(agent, [self._tool_finding("app/api.py")])
        assert len(merged) == 1
        assert merged[0].source == "agent"

    def test_absolute_path_is_what_used_to_leak_through(self):
        """Documents the old behaviour: without normalisation, both survive."""
        agent = [AgentFinding(path="app/api.py", line=2, title="Unsafe pickle use")]
        merged = merge_findings(agent, [self._tool_finding("/github/workspace/app/api.py")])
        assert len(merged) == 2


class TestSplitBySource:
    def _findings(self):
        return [
            AgentFinding(path="app/api.py", line=2, title="Agent one", source="agent"),
            AgentFinding(path="app/api.py", line=3, title="B403", source="bandit"),
            AgentFinding(path="app/api.py", line=3, title="rule", source="semgrep"),
        ]

    def test_partitions_correctly(self):
        agent, analyser = split_by_source(self._findings())
        assert [f.title for f in agent] == ["Agent one"]
        assert {f.source for f in analyser} == {"bandit", "semgrep"}

    def test_is_agent_finding(self):
        assert is_agent_finding(AgentFinding(path="a", title="t", source="agent"))
        assert not is_agent_finding(AgentFinding(path="a", title="t", source="ruff"))

    def test_only_agent_findings_become_comments(self):
        agent, _ = split_by_source(self._findings())
        comments, _ = build_inline_comments(agent, diff())
        assert len(comments) == 1

    def test_empty(self):
        assert split_by_source([]) == ([], [])


class TestHowToAct:
    def test_names_every_control_and_its_effect(self):
        block = "\n".join(format_how_to_act(2, "https://github.com/o/r/pull/1"))
        assert "Apply suggestion" in block and "commits it to this branch" in block
        assert "Resolve conversation" in block and "this is how you decline" in block
        assert "Do nothing" in block

    def test_states_the_merge_footgun(self):
        block = "\n".join(format_how_to_act(1, "https://github.com/o/r/pull/1"))
        assert "Merging this PR applies nothing" in block
        assert "discarded" in block

    def test_batching_points_at_the_files_tab(self):
        """The button exists in the Conversation tab but refuses to work there."""
        block = "\n".join(format_how_to_act(1, "https://github.com/o/r/pull/7"))
        assert "https://github.com/o/r/pull/7/files" in block
        assert "only works in the" in block

    def test_degrades_without_a_pr_url(self):
        block = "\n".join(format_how_to_act(1))
        assert "**Files changed**" in block
        assert "](" not in block  # no broken link

    def test_pluralises(self):
        assert "**1 suggested fix**" in "\n".join(format_how_to_act(1))
        assert "**3 suggested fixes**" in "\n".join(format_how_to_act(3))


class TestAnalyserTable:
    def _analyser(self, n=3):
        return [
            AgentFinding(
                path="app/api.py", line=i, title=f"R{i}", body=f"detail {i}",
                severity="low", source="bandit",
            )
            for i in range(1, n + 1)
        ]

    def test_renders_a_compact_table(self):
        out = "\n".join(format_analyser_table(self._analyser()))
        assert "### Static analysis" in out
        assert "| Severity | Location | Tool | Detail |" in out
        assert "`app/api.py`:1" in out and "`bandit`" in out

    def test_empty_renders_nothing(self):
        assert format_analyser_table([]) == []

    def test_truncates(self):
        out = "\n".join(format_analyser_table(self._analyser(60), limit=10))
        assert "50 further hits omitted" in out

    def test_pipes_in_detail_are_escaped(self):
        f = AgentFinding(path="a.py", line=1, title="E501: a | b", source="ruff")
        assert "E501: a \\| b" in "\n".join(format_analyser_table([f]))

    def test_detail_shows_the_rule_id(self):
        """from_tool_finding puts '<rule>: <message>' in the title."""
        f = AgentFinding(path="a.py", line=1, title="E501: line too long", source="ruff")
        assert "E501" in "\n".join(format_analyser_table([f]))


class TestReviewBodyCounts:
    def _agent(self):
        return [
            AgentFinding(path="app/api.py", line=2, title="Real one",
                         severity="critical", category="security", source="agent")
        ]

    def _analyser(self):
        return [
            AgentFinding(path="app/api.py", line=i, title=f"R{i}", severity="low",
                         category="security", source="bandit")
            for i in range(20)
        ]

    def test_severity_table_counts_agent_findings_only(self):
        """The bug: the table claimed 27 on a review that posted 8 comments."""
        body = build_review_body(
            self._agent(), summary="s", analyser_findings=self._analyser()
        )
        # Scope to the Findings section; Low appears later in the analyser table,
        # which is exactly the separation being asserted.
        findings_section = body.split("### Findings")[1].split("###")[0]
        assert "| 🔴 Critical | 1 |" in findings_section
        assert "Low" not in findings_section
        rows = [ln for ln in findings_section.splitlines() if ln.startswith("| ")]
        assert len(rows) == 2  # header + the single agent finding

    def test_analyser_hits_stated_separately(self):
        body = build_review_body(
            self._agent(), summary="s", analyser_findings=self._analyser()
        )
        assert "Plus 20 static-analysis hits" in body
        assert "### Static analysis" in body

    def test_singular_analyser_hit(self):
        body = build_review_body(
            self._agent(), summary="s", analyser_findings=self._analyser()[:1]
        )
        assert "Plus 1 static-analysis hit," in body

    def test_no_analyser_section_when_none(self):
        body = build_review_body(self._agent(), summary="s")
        assert "static-analysis hit" not in body
        assert "### Static analysis" not in body

    def test_how_to_act_only_when_a_fix_exists(self):
        from src.agent.fixes import Fix

        assert "How to act" not in build_review_body(self._agent(), summary="s")

        with_fix = self._agent()
        with_fix[0].fix = Fix(2, 2, "x", valid=True)
        body = build_review_body(with_fix, summary="s", pr_url="https://github.com/o/r/pull/1")
        assert "### How to act on this review" in body
        assert "Merging this PR applies nothing" in body
