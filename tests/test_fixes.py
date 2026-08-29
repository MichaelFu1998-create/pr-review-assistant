"""Applyable fixes: range support, validation, and suggestion rendering."""

import pytest

from src.agent.fixes import MAX_FIX_LINES, Fix, rejection_feedback, validate_fix
from src.agent.findings import AgentFinding
from src.diff.patch import DiffMap, FilePatch
from src.output.comments import build_inline_comments, format_finding_body
from src.output.summary import build_review_body, count_fixes

# Two hunks with a gap, so range checks have something real to reject.
PATCH = """@@ -1,4 +1,5 @@
 import os
-import sys
+import sqlite3
+import json
 
 def get_user(conn, user_id):
@@ -20,4 +21,5 @@ def get_user(conn, user_id):
 def run(conn, user_id):
     cur = conn.cursor()
-    cur.execute("SELECT * FROM users WHERE id = " + user_id)
+    cur.execute("SELECT * FROM users WHERE id = " + user_id)
+    return cur.fetchone()
     return None
"""


def diff():
    return DiffMap.from_pull_files({"app.py": {"patch": PATCH, "status": "modified"}})


class TestRangeText:
    def test_single_and_multi_line(self):
        fp = FilePatch.from_patch("app.py", PATCH)
        assert fp.range_text(2, 2) == ["import sqlite3"]
        assert fp.range_text(2, 3) == ["import sqlite3", "import json"]

    def test_context_lines_are_included(self):
        """Context lines are in the diff, so they are suggestable."""
        fp = FilePatch.from_patch("app.py", PATCH)
        assert fp.range_text(1, 1) == ["import os"]

    def test_range_spanning_a_hunk_gap_is_rejected(self):
        fp = FilePatch.from_patch("app.py", PATCH)
        assert fp.range_text(4, 22) is None
        assert fp.is_suggestable(4, 22) is False

    def test_out_of_diff_and_malformed_ranges(self):
        fp = FilePatch.from_patch("app.py", PATCH)
        assert fp.range_text(9000, 9001) is None
        assert fp.range_text(0, 2) is None
        assert fp.range_text(5, 1) is None

    def test_diffmap_delegates(self):
        dm = diff()
        assert dm.range_text("app.py", 2, 2) == ["import sqlite3"]
        assert dm.range_text("missing.py", 1, 1) is None
        assert dm.is_suggestable("app.py", 2, 3) is True
        assert dm.is_suggestable("missing.py", 1, 1) is False


class TestValidation:
    def _fix(self, **kw):
        base = dict(start_line=2, end_line=2, replacement="import sqlite3  # noqa")
        base.update(kw)
        return Fix(**base)

    def test_valid_fix_accepted(self):
        fix = validate_fix(self._fix(), "app.py", diff(), confidence="high")
        assert fix.valid and fix.rejected_because == ""

    def test_low_confidence_refused(self):
        """An Apply button on a shaky fix is worse than no fix."""
        fix = validate_fix(self._fix(), "app.py", diff(), confidence="medium")
        assert not fix.valid and "confidence" in fix.rejected_because

    def test_disabled_globally(self):
        fix = validate_fix(self._fix(), "app.py", diff(), "high", enabled=False)
        assert not fix.valid and "disabled" in fix.rejected_because

    def test_range_outside_the_diff(self):
        fix = validate_fix(self._fix(start_line=900, end_line=900), "app.py", diff(), "high")
        assert not fix.valid and "not all part of this PR's diff" in fix.rejected_because

    def test_range_spanning_a_hunk_gap(self):
        fix = validate_fix(self._fix(start_line=4, end_line=22), "app.py", diff(), "high")
        assert not fix.valid and "gap between hunks" in fix.rejected_because

    def test_unknown_file(self):
        fix = validate_fix(self._fix(), "nope.py", diff(), "high")
        assert not fix.valid

    @pytest.mark.parametrize("start,end", [(0, 2), (5, 1), (-3, -1)])
    def test_malformed_range(self, start, end):
        fix = validate_fix(self._fix(start_line=start, end_line=end), "app.py", diff(), "high")
        assert not fix.valid and "invalid range" in fix.rejected_because

    def test_oversized_range(self):
        fix = validate_fix(
            self._fix(start_line=1, end_line=MAX_FIX_LINES + 5), "app.py", diff(), "high"
        )
        assert not fix.valid and "limit is" in fix.rejected_because

    def test_no_op_replacement_refused(self):
        fix = validate_fix(self._fix(replacement="import sqlite3"), "app.py", diff(), "high")
        assert not fix.valid and "identical" in fix.rejected_because

    def test_code_fence_in_replacement_refused(self):
        fix = validate_fix(
            self._fix(replacement="```python\nimport x\n```"), "app.py", diff(), "high"
        )
        assert not fix.valid and "code fence" in fix.rejected_because

    def test_dropped_indentation_refused(self):
        """The classic failure: correct logic, flush-left, silently breaks the file."""
        fix = validate_fix(
            Fix(
                start_line=23,
                end_line=23,
                replacement='cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))',
            ),
            "app.py",
            diff(),
            "high",
        )
        assert not fix.valid and "flush-left" in fix.rejected_because

    def test_correctly_indented_replacement_accepted(self):
        fix = validate_fix(
            Fix(
                start_line=23,
                end_line=23,
                replacement='    cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))',
            ),
            "app.py",
            diff(),
            "high",
        )
        assert fix.valid

    def test_reindent_to_a_different_depth_is_allowed(self):
        fix = validate_fix(
            Fix(start_line=23, end_line=23, replacement='        pass'),
            "app.py", diff(), "high",
        )
        assert fix.valid


class TestRejectionFeedback:
    def test_echoes_the_true_original_text(self):
        fix = validate_fix(
            Fix(start_line=2, end_line=3, replacement="import sqlite3\nimport json"),
            "app.py", diff(), "high",
        )
        message = rejection_feedback(fix, "app.py", diff())
        assert "2| import sqlite3" in message
        assert "3| import json" in message
        assert "recorded either way" in message

    def test_points_at_read_diff_when_out_of_range(self):
        fix = validate_fix(Fix(900, 900, "x"), "app.py", diff(), "high")
        assert "read_diff" in rejection_feedback(fix, "app.py", diff())


class TestRendering:
    def _finding(self, fix=None, **kw):
        base = dict(
            path="app.py", line=2, severity="high", category="security",
            title="SQL injection", body="Concatenated input.", confidence="high",
        )
        base.update(kw)
        return AgentFinding(fix=fix, **base)

    def test_valid_fix_renders_a_suggestion_block(self):
        fix = validate_fix(Fix(2, 2, "import sqlite3  # ok"), "app.py", diff(), "high")
        body = format_finding_body(self._finding(fix=fix))
        assert "```suggestion" in body
        assert "apply directly" in body

    def test_rejected_fix_shows_the_code_and_says_why(self):
        fix = validate_fix(Fix(900, 900, "whatever"), "app.py", diff(), "high")
        body = format_finding_body(self._finding(fix=fix))
        assert "```suggestion" not in body
        assert "apply by hand" in body
        assert "whatever" in body  # the proposed code is still shown
        # the validation reason is surfaced, not left to be guessed at
        assert "No Apply button:" in body
        assert "not all part of this PR's diff" in body

    def test_single_line_fix_comment_payload(self):
        fix = validate_fix(Fix(2, 2, "import sqlite3  # ok"), "app.py", diff(), "high")
        comments, unanchored = build_inline_comments([self._finding(fix=fix)], diff())
        assert unanchored == []
        assert len(comments) == 1
        assert comments[0]["line"] == 2
        assert comments[0]["side"] == "RIGHT"
        assert "start_line" not in comments[0]

    def test_multi_line_fix_carries_start_line(self):
        fix = validate_fix(
            Fix(2, 3, "import sqlite3\nimport ujson"), "app.py", diff(), "high"
        )
        comments, _ = build_inline_comments([self._finding(fix=fix)], diff())
        assert comments[0]["start_line"] == 2
        assert comments[0]["start_side"] == "RIGHT"
        assert comments[0]["line"] == 3

    def test_fix_bearing_findings_are_never_merged(self):
        """Two ```suggestion blocks in one comment do not both render Apply."""
        a = self._finding(
            title="A", fix=validate_fix(Fix(2, 2, "import sqlite3  # a"), "app.py", diff(), "high")
        )
        b = self._finding(
            title="B", fix=validate_fix(Fix(2, 2, "import sqlite3  # b"), "app.py", diff(), "high")
        )
        comments, _ = build_inline_comments([a, b], diff())
        assert len(comments) == 2
        for comment in comments:
            assert comment["body"].count("```suggestion") == 1

    def test_findings_without_fixes_still_merge(self):
        comments, _ = build_inline_comments(
            [self._finding(title="A"), self._finding(title="B")], diff()
        )
        assert len(comments) == 1
        assert "A" in comments[0]["body"] and "B" in comments[0]["body"]

    def test_fix_anchors_to_its_own_range_not_the_finding_line(self):
        fix = validate_fix(Fix(23, 23, "    pass"), "app.py", diff(), "high")
        comments, _ = build_inline_comments([self._finding(line=2, fix=fix)], diff())
        assert comments[0]["line"] == 23


class TestCountsAndSummary:
    def _f(self, valid):
        fix = Fix(2, 2, "x", valid=valid, rejected_because="" if valid else "nope")
        return AgentFinding(path="app.py", line=2, title="t", severity="high", fix=fix)

    def test_counts(self):
        assert count_fixes([self._f(True), self._f(False), self._f(True)]) == (2, 1)
        assert count_fixes([]) == (0, 0)

    def test_summary_explains_how_to_apply(self):
        body = build_review_body([self._f(True)], summary="s")
        assert "1 suggested fix" in body
        assert "Apply suggestion" in body
        assert "Add suggestion to batch" in body

    def test_summary_pluralises(self):
        body = build_review_body([self._f(True), self._f(True)], summary="s")
        assert "2 suggested fixes" in body

    def test_footer_reports_rejected_fixes(self):
        body = build_review_body([self._f(True), self._f(False)], summary="s")
        assert "1 applyable fix(es), 1 not applyable" in body

    def test_no_fix_note_when_there_are_none(self):
        finding = AgentFinding(path="app.py", line=2, title="t", severity="high")
        assert "suggested fix" not in build_review_body([finding], summary="s")


class TestToolbeltIntegration:
    """post_finding validating a fix, and telling the agent how to correct it."""

    def _belt(self, suggest_fixes=True):
        from src.agent.budget import Budget
        from src.agent.context import PRMetadata, ReviewContext
        from src.agent.findings import FindingCollector
        from src.agent.toolbelt import Toolbelt

        context = ReviewContext(
            workspace=".", diff=diff(), metadata=PRMetadata(title="t")
        )
        return Toolbelt(
            context, FindingCollector(), Budget(), suggest_fixes=suggest_fixes
        )

    def _post(self, belt, **kw):
        from src.llm.base import ToolCall

        args = dict(
            path="app.py", line=2, severity="high", category="security",
            title="SQL injection", body="b", confidence="high",
        )
        args.update(kw)
        return belt.dispatch(ToolCall(id="c1", name="post_finding", arguments=args))

    def test_valid_fix_is_confirmed_to_the_agent(self):
        belt = self._belt()
        out = self._post(
            belt, fix_start_line=2, fix_end_line=2, fix_replacement="import sqlite3  # ok"
        ).text
        assert "Fix accepted as an applyable suggestion" in out
        assert belt.collector.findings[0].fix.valid

    def test_single_line_fix_may_omit_the_end_line(self):
        belt = self._belt()
        self._post(belt, fix_start_line=2, fix_replacement="import sqlite3  # ok")
        fix = belt.collector.findings[0].fix
        assert fix.start_line == fix.end_line == 2 and fix.valid

    def test_rejected_fix_still_records_the_finding(self):
        belt = self._belt()
        out = self._post(
            belt, fix_start_line=900, fix_end_line=900, fix_replacement="x"
        ).text
        assert "Fix not applyable" in out
        assert len(belt.collector) == 1
        assert belt.collector.findings[0].fix.valid is False

    def test_rejection_echoes_the_real_text_for_a_retry(self):
        belt = self._belt()
        out = self._post(
            belt, fix_start_line=2, fix_end_line=2, fix_replacement="import sqlite3"
        ).text
        assert "identical to the current code" in out
        assert "2| import sqlite3" in out

    def test_low_confidence_fix_refused(self):
        belt = self._belt()
        out = self._post(
            belt, confidence="low", fix_start_line=2, fix_end_line=2,
            fix_replacement="import sqlite3  # ok",
        ).text
        assert "confidence" in out and "Fix not applyable" in out

    def test_suggest_fixes_disabled(self):
        belt = self._belt(suggest_fixes=False)
        out = self._post(
            belt, fix_start_line=2, fix_end_line=2, fix_replacement="import sqlite3  # ok"
        ).text
        assert "disabled" in out

    def test_partial_fix_arguments_are_ignored(self):
        belt = self._belt()
        out = self._post(belt, fix_start_line=2).text  # no replacement
        assert "Fix" not in out
        assert belt.collector.findings[0].fix is None

    def test_read_lines_returns_text_without_a_gutter(self):
        from src.llm.base import ToolCall

        belt = self._belt()
        out = belt.dispatch(
            ToolCall(
                id="c1", name="read_lines",
                arguments={"path": "app.py", "start_line": 2, "end_line": 3},
            )
        ).text
        assert "import sqlite3\nimport json" in out
        assert "2 |" not in out and "     2" not in out

    def test_read_lines_requires_integers(self):
        from src.llm.base import ToolCall

        belt = self._belt()
        out = belt.dispatch(
            ToolCall(
                id="c1", name="read_lines",
                arguments={"path": "app.py", "start_line": "two", "end_line": 3},
            )
        ).text
        assert "must be integers" in out

    def test_fix_schema_is_advertised(self):
        belt = self._belt()
        schema = next(s for s in belt.schemas() if s.name == "post_finding")
        props = schema.parameters["properties"]
        assert {"fix_start_line", "fix_end_line", "fix_replacement"} <= set(props)
        assert any(s.name == "read_lines" for s in belt.schemas())


class TestReadLinesOutsideTheDiff:
    """The fallback path: a file in the checkout that this PR did not touch.

    Reachable whenever the agent inspects a caller, and not covered by the
    diff-backed path above.
    """

    @pytest.fixture
    def belt(self, tmp_path):
        from src.agent.budget import Budget
        from src.agent.context import ReviewContext
        from src.agent.findings import FindingCollector
        from src.agent.toolbelt import Toolbelt

        (tmp_path / "helper.py").write_text("def a():\n    return 1\n\ndef b():\n    return 2\n")
        context = ReviewContext(workspace=str(tmp_path), diff=diff())
        return Toolbelt(context, FindingCollector(), Budget())

    def _read(self, belt, **kw):
        from src.llm.base import ToolCall

        return belt.dispatch(ToolCall(id="c1", name="read_lines", arguments=kw)).text

    def test_reads_an_untouched_file(self, belt):
        out = self._read(belt, path="helper.py", start_line=1, end_line=2)
        assert "def a():\n    return 1" in out

    def test_warns_that_it_cannot_carry_a_fix(self, belt):
        out = self._read(belt, path="helper.py", start_line=1, end_line=2)
        assert "cannot carry an applyable fix" in out

    def test_refuses_to_escape_the_workspace(self, belt):
        out = self._read(belt, path="../../../etc/passwd", start_line=1, end_line=2)
        assert "outside the repository" in out

    def test_missing_file(self, belt):
        assert "does not exist" in self._read(belt, path="nope.py", start_line=1, end_line=2)

    def test_start_past_end_of_file(self, belt):
        assert "lines" in self._read(belt, path="helper.py", start_line=999, end_line=1000)


class TestFixAffordance:
    """A reader must be able to tell the three outcomes apart at a glance.

    Regression for testbed#2, where prose advice rendered inside a code fence
    looked like an applyable fix whose button had gone missing.
    """

    def _finding(self, **kw):
        base = dict(path="app.py", line=2, severity="high", category="security",
                    title="t", body="b", confidence="high")
        base.update(kw)
        return AgentFinding(**base)

    def test_the_three_outcomes_are_distinguishable(self):
        applyable = format_finding_body(
            self._finding(fix=validate_fix(Fix(2, 2, "import sqlite3  # ok"),
                                           "app.py", diff(), "high"))
        )
        refused = format_finding_body(
            self._finding(fix=validate_fix(Fix(900, 900, "x"), "app.py", diff(), "high"))
        )
        prose = format_finding_body(self._finding(suggested_fix="Use a real JWT library."))

        assert "```suggestion" in applyable
        assert "```suggestion" not in refused and "```" in refused
        assert "```" not in prose

        # each states its own situation
        assert "apply directly" in applyable
        assert "apply by hand" in refused
        assert "manual change" in prose

    def test_only_the_applyable_one_omits_the_no_button_note(self):
        applyable = format_finding_body(
            self._finding(fix=validate_fix(Fix(2, 2, "import sqlite3  # ok"),
                                           "app.py", diff(), "high"))
        )
        assert "No Apply button" not in applyable

    def test_refused_fix_names_the_specific_reason(self):
        for bad, expected in [
            (Fix(900, 900, "x"), "not all part of this PR's diff"),
            (Fix(2, 2, "import sqlite3"), "identical to the current code"),
        ]:
            body = format_finding_body(
                self._finding(fix=validate_fix(bad, "app.py", diff(), "high"))
            )
            assert expected in body, expected

    def test_low_confidence_fix_explains_itself(self):
        body = format_finding_body(
            self._finding(
                confidence="medium",
                fix=validate_fix(Fix(2, 2, "import sqlite3  # ok"), "app.py",
                                 diff(), "medium"),
            )
        )
        assert "No Apply button:" in body
        assert "only high-confidence findings" in body

    def test_no_fix_at_all_adds_nothing(self):
        body = format_finding_body(self._finding())
        assert "Apply button" not in body
        assert "Suggested fix" not in body and "How to fix" not in body

    def test_prose_survives_multiple_paragraphs(self):
        body = format_finding_body(
            self._finding(suggested_fix="First line.\n\nSecond line.")
        )
        assert "First line." in body and "Second line." in body
        assert "```" not in body
