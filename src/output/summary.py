"""The top-level review body: what the agent found, at a glance."""

from ..agent.findings import AgentFinding
from ..review.scoring import format_score_summary
from .comments import SEVERITY_LABEL, format_finding_body

SEVERITY_ORDER_DISPLAY = ("critical", "high", "medium", "low", "info")


def severity_counts(findings: list[AgentFinding]) -> dict[str, int]:
    counts = dict.fromkeys(SEVERITY_ORDER_DISPLAY, 0)
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


def format_code_scanning_note() -> list[str]:
    """Explain the second, failing check that uploading SARIF creates.

    GitHub's code scanning makes a "Code scanning results" check from the SARIF
    we upload and fails it whenever a pull request introduces new alerts —
    independent of this action's own fail_on. A successful run therefore shows a
    green tick *and* a red cross, and a red cross reads as "the pipeline broke"
    rather than "your code has issues".

    Deliberately [!NOTE] (blue) rather than [!CAUTION] (red): the job here is to
    defuse alarm, and a red callout explaining a red cross would compound it.
    """
    return [
        "> [!NOTE]",
        '> **The red ✗ on "Code scanning results" is expected.** It reports the',
        "> findings above as security alerts, and GitHub fails that check when a",
        "> pull request introduces new ones. The review workflow itself succeeded —",
        "> a red cross there means issues in the code, not a broken pipeline.",
        "> Full detail is under the repository's **Security → Code scanning** tab.",
        "",
    ]


def format_how_to_act(applyable: int, pr_url: str = "") -> list[str]:
    """Explain what the buttons GitHub renders actually do.

    We emit a ```suggestion block; GitHub decides which controls to draw, and it
    offers no reject button. Two things are consistently misread as a result:
    "Resolve conversation" looks like a decision about the code when it only
    marks a thread handled, and merging looks like it accepts what is pending
    when it silently discards every unapplied suggestion.

    Batching is also tab-specific — the button exists in the Conversation tab but
    refuses to work there — so the link points at Files changed.
    """
    plural = "fix" if applyable == 1 else "fixes"
    files_tab = f"{pr_url}/files" if pr_url else "Files changed"
    batch_cell = (
        f"Collects several into one commit — only works in the [Files changed]({files_tab}) tab"
        if pr_url
        else "Collects several into one commit — only works in the **Files changed** tab"
    )

    return [
        f"**{applyable} suggested {plural}** below can be applied directly.",
        "",
        "### How to act on this review",
        "",
        "| Action | What it does |",
        "|---|---|",
        "| **Apply suggestion** | Accepts the fix and commits it to this branch |",
        f"| **Add suggestion to batch** | {batch_cell} |",
        "| **Resolve conversation** | Marks the thread handled. Applies nothing — this is how you decline |",
        "| Do nothing | The suggestion is ignored |",
        "",
        "> **Merging this PR applies nothing.** Unapplied suggestions are discarded "
        "on merge — the branch merges exactly as it stands. Apply the ones you want first.",
        "",
    ]


def format_analyser_table(findings: list[AgentFinding], limit: int = 40) -> list[str]:
    """Render analyser hits compactly, rather than as full finding bodies."""
    if not findings:
        return []

    rows = [
        "### Static analysis",
        "",
        "_Raw analyser output, for reference. Anything important here is already "
        "explained in the comments above._",
        "",
        "| Severity | Location | Tool | Detail |",
        "|---|---|---|---|",
    ]
    for finding in findings[:limit]:
        location = f"`{finding.path}`"
        if finding.line:
            location += f":{finding.line}"
        # title carries "<rule_id>: <message>" for analyser findings
        # (AgentFinding.from_tool_finding); body is only the message.
        detail = (finding.title or finding.body).replace("|", "\\|").replace("\n", " ")[:120]
        rows.append(
            f"| {SEVERITY_LABEL.get(finding.severity, finding.severity)} | "
            f"{location} | `{finding.source}` | {detail} |"
        )
    if len(findings) > limit:
        rows.append(f"\n_{len(findings) - limit} further hits omitted._")
    rows.append("")
    return rows


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
    agent_mode: str = "agent",
    model: str = "",
    observations: dict[str, list[str]] | None = None,
    analyser_findings: list[AgentFinding] | None = None,
    pr_url: str = "",
    sarif_enabled: bool = False,
) -> str:
    """Assemble the review body posted above the inline comments."""
    parts = ["## Automated Code Review", ""]

    if summary:
        parts.extend([summary, ""])

    analyser_findings = analyser_findings or []

    parts.extend(["### Findings", "", format_severity_table(findings), ""])

    categories = format_category_line(findings)
    if categories:
        parts.extend([f"_{categories}_", ""])

    # Counted separately: mixing raw analyser hits into the table above made it
    # claim 27 findings on a review that posted 8 comments.
    if analyser_findings:
        n = len(analyser_findings)
        parts.extend(
            [
                f"Plus {n} static-analysis hit{'s' if n != 1 else ''}, listed below. "
                "The reviewer checked these and re-reported the ones that matter above.",
                "",
            ]
        )

    if sarif_enabled:
        parts.extend(format_code_scanning_note())

    applyable, _rejected = count_fixes(findings)
    if applyable:
        parts.extend(format_how_to_act(applyable, pr_url))

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

    parts.extend(format_analyser_table(analyser_findings))

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
