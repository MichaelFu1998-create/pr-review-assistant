"""Tests for unified-diff parsing and comment anchoring."""

from src.diff.patch import (
    ADDED,
    CONTEXT,
    REMOVED,
    DiffMap,
    FilePatch,
    parse_patch,
)

# A realistic two-hunk modification.
MODIFIED = """@@ -1,6 +1,7 @@
 import os
-import sys
+import sys
+import json
 
 def load(path):
     with open(path) as f:
         return f.read()
@@ -20,4 +21,5 @@ def load(path):
 def save(path, data):
     with open(path, "w") as f:
-        f.write(str(data))
+        f.write(json.dumps(data))
+        f.flush()
     return True
"""

NEW_FILE = """@@ -0,0 +1,3 @@
+def hello():
+    return "hi"
+
"""

SINGLE_LINE = """@@ -1 +1 @@
-old
+new
"""


class TestParsePatch:
    def test_empty_and_none(self):
        assert parse_patch(None) == []
        assert parse_patch("") == []

    def test_hunk_headers_parsed(self):
        hunks = parse_patch(MODIFIED)
        assert len(hunks) == 2
        assert (hunks[0].old_start, hunks[0].old_count) == (1, 6)
        assert (hunks[0].new_start, hunks[0].new_count) == (1, 7)
        assert (hunks[1].new_start, hunks[1].new_count) == (21, 5)
        assert hunks[1].heading == " def load(path):"

    def test_line_numbering_tracks_both_sides(self):
        hunks = parse_patch(MODIFIED)
        first = hunks[0].lines
        # " import os" is context at line 1 on both sides
        assert first[0].kind == CONTEXT
        assert (first[0].old_line, first[0].new_line) == (1, 1)
        # "-import sys" consumes an old line only
        assert first[1].kind == REMOVED
        assert (first[1].old_line, first[1].new_line) == (2, None)
        # the two "+" lines get consecutive new-side numbers
        assert first[2].kind == ADDED and first[2].new_line == 2
        assert first[3].kind == ADDED and first[3].new_line == 3

    def test_omitted_counts_default_to_one(self):
        hunk = parse_patch(SINGLE_LINE)[0]
        assert (hunk.old_count, hunk.new_count) == (1, 1)

    def test_explicit_zero_count_preserved_for_new_file(self):
        hunk = parse_patch(NEW_FILE)[0]
        assert hunk.old_count == 0
        assert hunk.new_count == 3

    def test_no_newline_marker_ignored(self):
        patch = "@@ -1,1 +1,1 @@\n-a\n\\ No newline at end of file\n+b\n"
        kinds = [line.kind for line in parse_patch(patch)[0].lines]
        assert kinds == [REMOVED, ADDED]

    def test_malformed_patch_does_not_raise(self):
        assert parse_patch("not a diff at all") == []


class TestFilePatch:
    def test_added_vs_commentable_lines(self):
        fp = FilePatch.from_patch("m.py", MODIFIED)
        # additions only
        assert fp.added_lines == {2, 3, 23, 24}
        # additions plus context — context lines are commentable too
        assert 1 in fp.commentable_lines
        assert fp.added_lines.issubset(fp.commentable_lines)
        # a removed line has no new-side number, so it is not commentable
        assert len(fp.commentable_lines) > len(fp.added_lines)

    def test_is_changed_distinguishes_new_from_context(self):
        fp = FilePatch.from_patch("m.py", MODIFIED)
        assert fp.is_changed(3) is True
        assert fp.is_changed(1) is False  # context, present but untouched

    def test_anchor_exact_hit(self):
        fp = FilePatch.from_patch("m.py", MODIFIED)
        assert fp.anchor(3) == 3

    def test_anchor_snaps_nearby_line(self):
        fp = FilePatch.from_patch("m.py", MODIFIED)
        # line 19 sits between the hunks; nearest commentable is in range
        assert fp.anchor(19) in fp.commentable_lines

    def test_anchor_rejects_distant_line(self):
        fp = FilePatch.from_patch("m.py", MODIFIED)
        assert fp.anchor(5000) is None

    def test_anchor_handles_none_and_empty(self):
        fp = FilePatch.from_patch("m.py", MODIFIED)
        assert fp.anchor(None) is None
        assert FilePatch.from_patch("bin.png", None).anchor(1) is None

    def test_annotated_pairs_lines_with_new_numbers(self):
        fp = FilePatch.from_patch("m.py", MODIFIED)
        out = fp.annotated()
        assert "@@ -1,6 +1,7 @@" in out
        assert "     3 + import json" in out
        # removed lines get a blank gutter, since they have no new-side number
        assert "       - import sys" in out

    def test_annotated_without_hunks_explains_itself(self):
        out = FilePatch.from_patch("logo.png", None, status="added").annotated()
        assert "no textual diff" in out
        assert "logo.png" in out

    def test_annotated_truncates(self):
        fp = FilePatch.from_patch("m.py", MODIFIED)
        out = fp.annotated(max_lines=4)
        assert "truncated" in out
        assert len(out.split("\n")) <= 6


class TestDiffMap:
    def _map(self):
        return DiffMap.from_pull_files(
            {
                "m.py": {"patch": MODIFIED, "status": "modified"},
                "new.py": {"patch": NEW_FILE, "status": "added"},
            }
        )

    def test_from_pull_files_shape(self):
        dm = self._map()
        assert len(dm) == 2
        assert "m.py" in dm
        assert sorted(dm.paths) == ["m.py", "new.py"]

    def test_read_diff_unknown_path_lists_known(self):
        out = self._map().read_diff("nope.py")
        assert "No diff" in out
        assert "m.py" in out

    def test_anchor_and_is_changed_delegate(self):
        dm = self._map()
        assert dm.anchor("m.py", 3) == 3
        assert dm.anchor("nope.py", 3) is None
        assert dm.is_changed("new.py", 1) is True
        assert dm.is_changed("nope.py", 1) is False

    def test_stats_counts_both_sides(self):
        dm = self._map()
        assert dm.stats("m.py") == (4, 2)
        assert dm.stats("new.py") == (3, 0)
        assert dm.stats("missing.py") == (0, 0)
