"""Applyable fixes, rendered as GitHub suggested changes.

A ``` ```suggestion ``` block in a review comment gives the author an Apply
button. Applying one produces a commit; the author can apply them one at a time
or batch several into a single commit. That is the accept/reject-per-edit flow,
and it is native — this tool never pushes code, which also means a fix cannot
land without a human click. That matters because the agent reads untrusted
repository contents.

The catch is that a suggestion replaces the commented range **verbatim**. Every
fix is therefore validated against the parsed diff before it is rendered.
Validation is not a policy knob: ``safe_create_review`` degrades the entire
review to a plain summary on a 422, so one out-of-range suggestion would strip
the inline comments from every other finding too.
"""

import logging
from dataclasses import dataclass

from ..diff.patch import DiffMap

logger = logging.getLogger(__name__)

# A suggestion bigger than this stops being reviewable at a glance, which is the
# whole point of an inline Apply button.
MAX_FIX_LINES = 40

# Only high-confidence findings may carry an Apply button. A plausible-looking
# wrong fix is worse than no fix, because it is one click from being committed.
REQUIRED_CONFIDENCE = "high"


@dataclass
class Fix:
    """A replacement for an exact inclusive line range on the new side."""

    start_line: int
    end_line: int
    replacement: str
    valid: bool = False
    rejected_because: str = ""

    @property
    def is_multiline(self) -> bool:
        return self.end_line > self.start_line

    def to_dict(self) -> dict:
        return {
            "start_line": self.start_line,
            "end_line": self.end_line,
            "replacement": self.replacement,
            "valid": self.valid,
            "rejected_because": self.rejected_because,
        }


def _leading_ws(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def validate_fix(
    fix: Fix,
    path: str,
    diff: DiffMap,
    confidence: str = "high",
    enabled: bool = True,
) -> Fix:
    """Decide whether a fix can be posted as a suggestion.

    Always returns the fix; ``valid`` and ``rejected_because`` say what was
    decided. A rejected fix is not discarded — it degrades to a plain fenced
    code block, so the author still sees the proposed code.
    """
    fix.valid = False

    if not enabled:
        fix.rejected_because = "suggest_fixes is disabled"
        return fix

    if confidence != REQUIRED_CONFIDENCE:
        fix.rejected_because = (
            f"confidence is '{confidence}'; only {REQUIRED_CONFIDENCE}-confidence "
            "findings may carry an applyable fix"
        )
        return fix

    if fix.start_line < 1 or fix.end_line < fix.start_line:
        fix.rejected_because = (
            f"invalid range {fix.start_line}-{fix.end_line}: "
            "start_line must be >= 1 and <= end_line"
        )
        return fix

    span = fix.end_line - fix.start_line + 1
    if span > MAX_FIX_LINES:
        fix.rejected_because = (
            f"range covers {span} lines; the limit is {MAX_FIX_LINES}. "
            "Propose a smaller, targeted fix."
        )
        return fix

    original = diff.range_text(path, fix.start_line, fix.end_line)
    if original is None:
        fix.rejected_because = (
            f"lines {fix.start_line}-{fix.end_line} of '{path}' are not all part "
            "of this PR's diff. GitHub only accepts a suggestion on lines shown "
            "in the diff, and the range must not span a gap between hunks."
        )
        return fix

    replacement_lines = fix.replacement.split("\n")

    if "```" in fix.replacement:
        fix.rejected_because = "replacement contains a code fence, which would break the suggestion block"
        return fix

    if replacement_lines == original:
        fix.rejected_because = "replacement is identical to the current code"
        return fix

    # The classic failure: the model rewrites the logic correctly but drops the
    # indentation, so applying it silently breaks the file.
    original_indent = _leading_ws(original[0])
    first_content = next((line for line in replacement_lines if line.strip()), "")
    if original_indent and first_content and not _leading_ws(first_content):
        fix.rejected_because = (
            f"replacement is flush-left but line {fix.start_line} is indented with "
            f"{len(original_indent)} character(s). Include the original leading "
            "whitespace on every line."
        )
        return fix

    fix.valid = True
    fix.rejected_because = ""
    return fix


def rejection_feedback(fix: Fix, path: str, diff: DiffMap) -> str:
    """What to tell the agent when its fix cannot be posted.

    Echoes the true current text, so the agent can correct the replacement
    instead of guessing at what it got wrong.
    """
    lines = [f"Fix not applyable: {fix.rejected_because}"]
    original = diff.range_text(path, fix.start_line, fix.end_line)
    if original is not None:
        lines.append(
            f"The current text of {path} lines {fix.start_line}-{fix.end_line} is:"
        )
        lines.extend(
            f"{fix.start_line + i}| {text}" for i, text in enumerate(original)
        )
        lines.append(
            "Re-post the finding with a replacement for exactly these lines, "
            "keeping their indentation."
        )
    else:
        lines.append(
            "Call read_diff to see which lines are part of this PR's diff; a "
            "suggestion can only replace lines shown there."
        )
    lines.append("The finding itself was recorded either way.")
    return "\n".join(lines)
