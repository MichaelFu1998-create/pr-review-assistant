"""The top-level review body: what the agent found, at a glance."""

from ..agent.findings import AgentFinding
from ..review.scoring import format_score_summary
from .comments import SEVERITY_LABEL, format_finding_body

SEVERITY_ORDER_DISPLAY = ("critical", "high", "medium", "low", "info")


def severity_counts(findings: list[AgentFinding]) -> dict[str, int]:
    counts = {s: 0 for s in SEVERITY_ORDER_DISPLAY}
    for finding in findings:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    return counts


def format_severity_table(findings: list[AgentFinding]) -> str:
    counts = severity_counts(findings)
    present = [(s, n) for s, n in counts.items() if n]
    if not present:
        return "No issues found."

    rows = ["| Severity | Count |", "|---|---|"]
    rows.extend(f"| {SEVERITY_LABEL.get(s, s)} | {n} |" for s, n in present)
    return "\n".join(rows)


def count_fixes(findings: list[AgentFinding]) -> tuple[int, int]:
    """(applyable, rejected) fix counts.

    The rejected count is the honest half: it says how often the agent proposed
    a fix that could not be turned into a suggestion.
    """
    applyable = sum(1 for f in findings if f.fix is not None and f.fix.valid)
    rejected = sum(1 for f in findings if f.fix is not None and not f.fix.valid)
    return applyable, rejected


def format_category_line(findings: list[AgentFinding]) -> str:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.category] = counts.get(finding.category, 0) + 1
    if not counts:
        return ""
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return " · ".join(f"{category}: {n}" for category, n in ordered)


def build_review_body(
    findings: list[AgentFinding],
    summary: str,
    scores: dict | None = None,
    unanchored: list[AgentFinding] | None = None,
    tools_used: list[str] | None = None,
    budget: dict | None = None,
    agent_mode: str = "single",
    model: str = "",
    observations: dict[str, list[str]] | None = None,
) -> str:
    """Assemble the review body posted above the inline comments."""
    parts = ["## Automated Code Review", ""]

    if summary:
        parts.extend([summary, ""])

    parts.extend(["### Findings", "", format_severity_table(findings), ""])

    categories = format_category_line(findings)
    if categories:
        parts.extend([f"_{categories}_", ""])

    applyable, _rejected = count_fixes(findings)
    if applyable:
        plural = "fix" if applyable == 1 else "fixes"
        parts.extend(
            [
                f"**{applyable} suggested {plural}** can be applied directly from "
                "the inline comments — use **Apply suggestion** to commit one, or "
                "**Add suggestion to batch** to commit several together.",
                "",
            ]
        )

    if scores:
        table = format_score_summary(scores)
        if table:
            parts.extend(["### Scores", "", table, ""])

    # Findings that could not be pinned to a diff line still belong somewhere.
    if unanchored:
        parts.extend(
            [
                "### Additional findings",
                "",
                "_These concern lines outside this PR's diff, so GitHub will not "
                "accept an inline comment on them._",
                "",
            ]
        )
        for finding in unanchored:
            location = f"`{finding.path}`"
            if finding.line:
                location += f" line {finding.line}"
            parts.extend([f"**{location}**", "", format_finding_body(finding), "", "---", ""])

    if observations:
        for heading, lines in observations.items():
            if lines:
                parts.extend([f"### {heading}", ""])
                parts.extend(f"- {line}" for line in lines)
                parts.append("")

    parts.append(_footer(tools_used, budget, agent_mode, model, count_fixes(findings)))
    return "\n".join(parts)


def _footer(
    tools_used: list[str] | None,
    budget: dict | None,
    agent_mode: str,
    model: str,
    fixes: tuple[int, int] | None = None,
) -> str:
    """A one-line account of what the run actually did.

    Students should be able to see the cost and depth of their review without
    digging through action logs.
    """
    bits = [f"mode: `{agent_mode}`"]
    if model:
        bits.append(f"model: `{model}`")
    if fixes and (fixes[0] or fixes[1]):
        applyable, rejected = fixes
        note = f"{applyable} applyable fix(es)"
        if rejected:
            note += f", {rejected} not applyable"
        bits.append(note)
    if tools_used:
        bits.append(f"analysers: {', '.join(tools_used)}")
    if budget:
        bits.append(f"{budget.get('steps', 0)} steps")
        bits.append(f"{budget.get('total_tokens', 0):,} tokens")
        bits.append(f"{budget.get('elapsed_seconds', 0)}s")
        stop = budget.get("stop_reason")
        if stop and stop != "completed":
            bits.append(f"**stopped early: {stop}**")
    return f"<sub>{' · '.join(bits)}</sub>"
