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

    parts.extend(_format_fix(finding))

    footer = [f"source: {finding.source}"]
    if finding.evidence:
        footer.append("evidence: " + ", ".join(finding.evidence[:4]))
    parts.extend(["", f"<sub>{' · '.join(footer)}</sub>"])

    return "\n".join(parts)


def is_agent_finding(finding: AgentFinding) -> bool:
    """True for findings the agent reasoned about, false for raw analyser hits."""
    return finding.source == "agent"


def split_by_source(
    findings: list[AgentFinding],
) -> tuple[list[AgentFinding], list[AgentFinding]]:
    """Partition into (agent findings, analyser findings).

    Only the agent's findings become inline comments. The agent is prompted to
    validate analyser output and re-report the real hits in its own words with
    an explanation, so posting both would comment twice on the same line — once
    with reasoning, once with a rule id.
    """
    agent = [f for f in findings if is_agent_finding(f)]
    analyser = [f for f in findings if not is_agent_finding(f)]
    return agent, analyser


def _format_fix(finding: AgentFinding) -> list[str]:
    """Render whichever kind of fix the finding carries.

    Three outcomes, and the reader has to be able to tell them apart at a
    glance. Previously all three used a code fence, so free-text advice looked
    like applyable code that had somehow lost its button — which is precisely
    the confusion this exists to prevent.
    """
    fix = finding.fix

    if fix is not None and fix.valid:
        # GitHub renders an Apply button for a suggestion block, and applying it
        # replaces exactly the commented range. Validated in agent/fixes.py,
        # because an out-of-range suggestion 422s the whole review.
        return ["", "**Suggested fix** — apply directly:", "```suggestion", fix.replacement, "```"]

    # The two non-applyable states get a red CAUTION callout. GitHub renders its
    # own prominent green box and Apply button for a suggestion, so an applyable
    # fix needs no extra colour; these two are the ones that otherwise read as a
    # fix whose button has gone missing.
    if fix is not None and fix.replacement:
        # Validation refused it. Still show the code — it is useful — but say why
        # there is no button rather than leaving it to be guessed at.
        reason = fix.rejected_because or "it could not be validated against this diff"
        return [
            "",
            "> [!CAUTION]",
            f"> **No Apply button** — {reason}. Apply this by hand, or",
            "> **Resolve conversation** to decline.",
            "",
            "```",
            fix.replacement,
            "```",
        ]

    if finding.suggested_fix:
        # Free-text advice. Not code, so it must not sit in a code fence: a fence
        # renders prose as a horizontally scrolling monospace line and implies it
        # is something you could apply.
        return [
            "",
            "> [!CAUTION]",
            "> **No Apply button** — this needs changes beyond the lines commented",
            "> on. Make them by hand, or **Resolve conversation** to decline.",
            "",
            finding.suggested_fix.strip(),
        ]

    return []


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
