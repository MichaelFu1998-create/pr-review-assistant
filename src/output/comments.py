"""Turn structured findings into GitHub review comments.

v1 pinned every comment to ``position: 1``, so nothing landed on the line it
described. Findings now carry a line number, which the diff map resolves to a
line GitHub will actually accept.

A finding that cannot be anchored is not dropped — it is returned to the caller
for the summary body, because a real issue on an unchanged line is still worth
saying.
"""

import logging
from urllib.parse import quote

from ..agent.findings import AgentFinding
from ..diff.patch import DiffMap

logger = logging.getLogger(__name__)

SEVERITY_LABEL = {
    "critical": "🔴 Critical",
    "high": "🟠 High",
    "medium": "🟡 Medium",
    "low": "🔵 Low",
    "info": "⚪ Info",
}


def encode_path(path: str) -> str:
    """Percent-encode a path for the review API.

    Next.js route directories such as ``src/app/[country]/page.tsx`` make the
    inline-comment API reject the whole review with a 422; encoding the brackets
    fixes it. Slashes stay literal.
    """
    return quote(path, safe="/")


def format_finding_body(finding: AgentFinding) -> str:
    """Render one finding as markdown."""
    label = SEVERITY_LABEL.get(finding.severity, finding.severity.title())
    meta = [f"**{label}**", f"`{finding.category}`"]
    if finding.cwe:
        meta.append(f"[{finding.cwe}](https://cwe.mitre.org/data/definitions/{finding.cwe.split('-')[1]}.html)")
    if finding.confidence != "high":
        meta.append(f"_{finding.confidence} confidence_")

    parts = [" · ".join(meta), "", f"**{finding.title}**", ""]
    if finding.body:
        parts.append(finding.body)

    fix = finding.fix
    if fix is not None and fix.valid:
        # A real suggestion block: GitHub renders an Apply button, and applying
        # it replaces exactly the commented range. Validated in agent/fixes.py,
        # because an out-of-range suggestion 422s the whole review.
        parts.extend(
            ["", "**Suggested fix** — apply directly:", "```suggestion", fix.replacement, "```"]
        )
    elif fix is not None and fix.replacement:
        # Validation refused it, so show the code without an Apply button rather
        # than dropping the agent's proposed fix entirely.
        parts.extend(["", "**Suggested fix** (not auto-applyable):", "```", fix.replacement, "```"])
    elif finding.suggested_fix:
        parts.extend(["", "**Suggested fix:**", "```", finding.suggested_fix.strip(), "```"])

    footer = [f"source: {finding.source}"]
    if finding.evidence:
        footer.append("evidence: " + ", ".join(finding.evidence[:4]))
    parts.extend(["", f"<sub>{' · '.join(footer)}</sub>"])

    return "\n".join(parts)


def build_inline_comments(
    findings: list[AgentFinding],
    diff: DiffMap,
) -> tuple[list[dict], list[AgentFinding]]:
    """Split findings into anchored inline comments and unanchorable leftovers.

    Findings that resolve to the same line are merged into a single comment;
    three separate comments on one line reads as noise.
    """
    grouped: dict[tuple[str, int], list[AgentFinding]] = {}
    unanchored: list[AgentFinding] = []
    comments: list[dict] = []

    for finding in findings:
        fix = finding.fix
        if fix is not None and fix.valid:
            # A suggestion must be alone in its comment — two ```suggestion
            # blocks in one body do not both render an Apply button — and it is
            # anchored to the fix's own range, not to the finding's line.
            finding.anchored_line = fix.start_line
            comment = {
                "path": encode_path(finding.path),
                "line": fix.end_line,
                "side": "RIGHT",
                "body": format_finding_body(finding),
            }
            if fix.is_multiline:
                comment["start_line"] = fix.start_line
                comment["start_side"] = "RIGHT"
            comments.append(comment)
            continue

        line = diff.anchor(finding.path, finding.line)
        if line is None:
            unanchored.append(finding)
            continue
        finding.anchored_line = line
        grouped.setdefault((finding.path, line), []).append(finding)

    for (path, line), group in grouped.items():
        bodies = [format_finding_body(f) for f in group]
        comments.append(
            {
                "path": encode_path(path),
                "line": line,
                "side": "RIGHT",
                "body": "\n\n---\n\n".join(bodies),
            }
        )

    applyable = sum(1 for f in findings if f.fix is not None and f.fix.valid)
    logger.info(
        "Anchored %d finding(s) into %d comment(s) (%d applyable fix(es)); "
        "%d could not be anchored",
        len(findings) - len(unanchored),
        len(comments),
        applyable,
        len(unanchored),
    )
    return comments, unanchored
