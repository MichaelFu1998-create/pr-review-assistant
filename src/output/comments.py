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

    if finding.suggested_fix:
        # A plain fence, not a ```suggestion block: a suggestion must replace the
        # commented range exactly, and an approximate one produces a broken
        # "Apply" button rather than a helpful edit.
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

    for finding in findings:
        line = diff.anchor(finding.path, finding.line)
        if line is None:
            unanchored.append(finding)
            continue
        finding.anchored_line = line
        grouped.setdefault((finding.path, line), []).append(finding)

    comments = []
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

    logger.info(
        "Anchored %d finding(s) into %d comment(s); %d could not be anchored",
        len(findings) - len(unanchored),
        len(comments),
        len(unanchored),
    )
    return comments, unanchored
