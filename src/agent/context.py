"""Everything the agent is allowed to look at during one review.

Holds the checkout, the parsed diff, PR metadata, and the pre-pass analyser
findings. The toolbelt reads exclusively through this object, which is what
keeps the agent read-only and inside the workspace.
"""

import logging
import os
import threading
from dataclasses import dataclass, field

from ..diff.patch import DiffMap
from ..tools.base import Finding

logger = logging.getLogger(__name__)

# Refuse to read anything larger than this through read_file; a multi-megabyte
# minified bundle would blow the context window in a single tool call.
MAX_READ_BYTES = 400_000


@dataclass
class PRMetadata:
    title: str = ""
    description: str = ""
    author: str = ""
    comments: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    base_ref: str = ""
    head_sha: str = ""


@dataclass
class ReviewContext:
    workspace: str
    diff: DiffMap
    metadata: PRMetadata = field(default_factory=PRMetadata)
    tool_findings: list[Finding] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    readme: str = ""
    # run_analyzer can be reached while tools/runner.py is still executing
    # analysers on its own thread pool, so these appends must not interleave.
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record_analyzer_run(self, tool_name: str, findings: list[Finding]) -> None:
        """Thread-safely fold an on-demand analyser run into the shared context."""
        with self._lock:
            self.tool_findings.extend(findings)
            if tool_name not in self.tools_used:
                self.tools_used.append(tool_name)

    @property
    def changed_paths(self) -> list[str]:
        return self.diff.paths

    def resolve(self, path: str) -> str | None:
        """Resolve a repo-relative path to an absolute one inside the workspace.

        Returns None for anything that escapes the checkout. The agent is
        driven by model output and by file contents it reads, both of which can
        carry `../..` — treat every path as untrusted.
        """
        if not path:
            return None
        workspace = os.path.realpath(self.workspace)
        candidate = os.path.realpath(os.path.join(workspace, path))
        if candidate != workspace and not candidate.startswith(workspace + os.sep):
            logger.warning("Refusing path outside the workspace: %s", path)
            return None
        return candidate

    def read_file(
        self, path: str, start: int | None = None, end: int | None = None
    ) -> str:
        """Read a file with line numbers, optionally a slice of it."""
        resolved = self.resolve(path)
        if resolved is None:
            return f"Error: '{path}' is outside the repository."
        if not os.path.isfile(resolved):
            return f"Error: '{path}' does not exist in the checkout."

        size = os.path.getsize(resolved)
        if size > MAX_READ_BYTES and start is None:
            return (
                f"Error: '{path}' is {size} bytes, too large to read whole. "
                "Call read_file again with start and end line numbers."
            )

        try:
            with open(resolved, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except OSError as e:
            return f"Error reading '{path}': {e}"

        total = len(lines)
        first = max(start or 1, 1)
        last = min(end or total, total)
        if first > total:
            return f"Error: '{path}' has {total} lines; {first} is past the end."

        selected = lines[first - 1 : last]
        numbered = "".join(
            f"{first + i:>6} | {line.rstrip(chr(10))}\n" for i, line in enumerate(selected)
        )
        header = f"# {path} (lines {first}-{last} of {total})\n"
        return header + numbered

    def findings_for(self, path: str) -> list[Finding]:
        return [f for f in self.tool_findings if f.file == path]

    def manifest(self) -> str:
        """The changed-file listing the agent starts from."""
        if not self.changed_paths:
            return "No files changed."

        rows = []
        for path in self.changed_paths:
            added, removed = self.diff.stats(path)
            file_patch = self.diff.get(path)
            status = file_patch.status if file_patch else "modified"
            hits = len(self.findings_for(path))
            hit_note = f", {hits} tool finding(s)" if hits else ""
            rows.append(f"- `{path}` ({status}, +{added}/-{removed}{hit_note})")
        return "\n".join(rows)
