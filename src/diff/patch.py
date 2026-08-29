"""Unified-diff parsing, line maps, and comment anchoring.

GitHub's ``PullRequestFile.patch`` carries only the hunks — no ``diff --git``
or ``---``/``+++`` headers — so the text starts directly at the first ``@@``.

Two things depend on this module:

1. The agent's ``read_diff`` tool. v1 only ever showed the model whole-file
   contents, so it could not tell changed code from pre-existing code. The
   annotated rendering here pairs every line with its *new-file* line number,
   which is what a finding has to cite to be anchorable.
2. Comment anchoring. GitHub accepts an inline comment only on a line that is
   part of the diff; ``DiffMap.anchor`` resolves a finding's line to a real,
   commentable one (or ``None``, in which case the caller demotes the finding
   to the summary body).
"""

import re
from dataclasses import dataclass, field

# Hunk header: @@ -old_start[,old_count] +new_start[,new_count] @@[ heading]
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")

ADDED = "add"
REMOVED = "del"
CONTEXT = "context"

# Max distance ``anchor`` will snap a cited line by before giving up. Beyond
# this a cited line is a hallucination, not an off-by-a-few.
_SNAP_DISTANCE = 20


@dataclass(frozen=True)
class DiffLine:
    """One line of a hunk.

    ``old_line`` is None for additions, ``new_line`` is None for removals;
    context lines carry both.
    """

    kind: str
    old_line: int | None
    new_line: int | None
    text: str


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    heading: str = ""
    lines: list[DiffLine] = field(default_factory=list)

    @property
    def new_end(self) -> int:
        """Last line number this hunk covers on the new side (inclusive)."""
        return self.new_start + max(self.new_count, 1) - 1

    def header(self) -> str:
        old = f"-{self.old_start},{self.old_count}"
        new = f"+{self.new_start},{self.new_count}"
        return f"@@ {old} {new} @@{self.heading}"


def parse_patch(patch: str | None) -> list[Hunk]:
    """Parse a unified diff body into hunks.

    Tolerant by design: GitHub occasionally emits ``\\ No newline at end of
    file`` markers and empty patches (binary files, pure renames), and a
    malformed hunk should degrade to "no anchors" rather than crash a review.
    """
    if not patch:
        return []

    hunks: list[Hunk] = []
    current: Hunk | None = None
    old_no = new_no = 0

    lines = patch.split("\n")
    # A patch ending in a newline yields a trailing "" that is an artifact of
    # the split, not a blank context line. Left in, it fabricates a commentable
    # line and desynchronises every line number after it. A genuine blank
    # context line is " " (space-prefixed), which survives this.
    if lines and lines[-1] == "":
        lines.pop()

    for raw in lines:
        match = HUNK_RE.match(raw)
        if match:
            old_start, old_count, new_start, new_count, heading = match.groups()
            current = Hunk(
                old_start=int(old_start),
                # A missing count means 1 ("@@ -1 +1 @@"); an explicit 0 means
                # the file is being created or deleted and must stay 0.
                old_count=1 if old_count is None else int(old_count),
                new_start=int(new_start),
                new_count=1 if new_count is None else int(new_count),
                heading=heading,
            )
            hunks.append(current)
            old_no = current.old_start
            new_no = current.new_start
            continue

        if current is None:
            # Preamble before the first @@ (or a patch that has none).
            continue

        if raw.startswith("\\"):
            # "\ No newline at end of file" — annotates the previous line.
            continue

        marker, text = (raw[:1], raw[1:]) if raw else (" ", "")

        if marker == "+":
            current.lines.append(DiffLine(ADDED, None, new_no, text))
            new_no += 1
        elif marker == "-":
            current.lines.append(DiffLine(REMOVED, old_no, None, text))
            old_no += 1
        elif marker == " ":
            current.lines.append(DiffLine(CONTEXT, old_no, new_no, text))
            old_no += 1
            new_no += 1
        else:
            # Not a diff line (trailing blank, stray metadata). Ignore it
            # rather than letting it desynchronise the line counters.
            continue

    return hunks


@dataclass
class FilePatch:
    """A single file's diff, plus the line maps derived from it."""

    path: str
    status: str = "modified"
    patch: str = ""
    hunks: list[Hunk] = field(default_factory=list)

    @classmethod
    def from_patch(cls, path: str, patch: str | None, status: str = "modified") -> "FilePatch":
        return cls(path=path, status=status, patch=patch or "", hunks=parse_patch(patch))

    @property
    def added_lines(self) -> set[int]:
        """New-side line numbers this PR actually introduced."""
        return {
            line.new_line
            for hunk in self.hunks
            for line in hunk.lines
            if line.kind == ADDED and line.new_line is not None
        }

    @property
    def commentable_lines(self) -> set[int]:
        """New-side lines GitHub will accept a RIGHT-side comment on.

        That is additions plus context: every line the diff actually renders.
        """
        return {
            line.new_line
            for hunk in self.hunks
            for line in hunk.lines
            if line.new_line is not None and line.kind in (ADDED, CONTEXT)
        }

    def is_changed(self, line: int) -> bool:
        return line in self.added_lines

    def anchor(self, line: int | None) -> int | None:
        """Resolve ``line`` to a commentable new-side line, or None.

        An agent citing a line just outside a hunk is common and recoverable,
        so we snap to the nearest commentable line rather than dropping the
        finding. Ties break toward the earlier line, which reads as pointing at
        the start of a construct rather than past its end.
        """
        commentable = self.commentable_lines
        if not commentable:
            return None
        if line is None:
            return None
        if line in commentable:
            return line

        # Only snap within a hunk's neighbourhood; a line hundreds of lines away
        # is a hallucination, not an off-by-a-few, and would anchor nonsense.
        nearest = min(commentable, key=lambda c: (abs(c - line), c))
        return nearest if abs(nearest - line) <= _SNAP_DISTANCE else None

    def annotated(self, max_lines: int | None = None) -> str:
        """Render the diff with new-file line numbers, for the LLM.

        Format is ``<lineno> <marker> <text>``; removed lines have no new-side
        number and so show a blank gutter. This is what lets the model cite a
        line number that ``anchor`` can actually resolve.
        """
        if not self.hunks:
            return f"(no textual diff available for `{self.path}`, status: {self.status})"

        out: list[str] = []
        truncated = False
        for hunk in self.hunks:
            out.append(hunk.header())
            for line in hunk.lines:
                if max_lines is not None and len(out) >= max_lines:
                    truncated = True
                    break
                gutter = f"{line.new_line:>6}" if line.new_line is not None else " " * 6
                marker = {ADDED: "+", REMOVED: "-", CONTEXT: " "}[line.kind]
                out.append(f"{gutter} {marker} {line.text}")
            if truncated:
                break

        if truncated:
            out.append("... [diff truncated to fit token budget]")
        return "\n".join(out)



class DiffMap:
    """All changed files in a PR, keyed by path."""

    def __init__(self, files: dict[str, FilePatch] | None = None):
        self._files: dict[str, FilePatch] = files or {}

    @classmethod
    def from_pull_files(cls, files: dict[str, dict]) -> "DiffMap":
        """Build from the dict shape ``github_client.files_for_review`` returns."""
        return cls(
            {
                path: FilePatch.from_patch(
                    path, info.get("patch"), info.get("status", "modified")
                )
                for path, info in files.items()
            }
        )

    def __contains__(self, path: str) -> bool:
        return path in self._files

    def __len__(self) -> int:
        return len(self._files)

    @property
    def paths(self) -> list[str]:
        return list(self._files)

    def get(self, path: str) -> FilePatch | None:
        return self._files.get(path)

    def read_diff(self, path: str, max_lines: int | None = None) -> str:
        file_patch = self._files.get(path)
        if file_patch is None:
            known = ", ".join(sorted(self._files)[:20]) or "(none)"
            return f"No diff for `{path}`. Changed files: {known}"
        return file_patch.annotated(max_lines=max_lines)

    def anchor(self, path: str, line: int | None) -> int | None:
        file_patch = self._files.get(path)
        return file_patch.anchor(line) if file_patch else None

    def is_changed(self, path: str, line: int) -> bool:
        file_patch = self._files.get(path)
        return bool(file_patch and file_patch.is_changed(line))

    def stats(self, path: str) -> tuple[int, int]:
        """(additions, deletions) for a path."""
        file_patch = self._files.get(path)
        if not file_patch:
            return (0, 0)
        added = sum(
            1 for h in file_patch.hunks for line in h.lines if line.kind == ADDED
        )
        removed = sum(
            1 for h in file_patch.hunks for line in h.lines if line.kind == REMOVED
        )
        return (added, removed)
