"""Machine-readable review report.

Written as a workflow artifact. For a capstone course this is the interesting
output: it makes reviews comparable across a cohort and over time, which
markdown in a PR thread never could.
"""

import json
from datetime import datetime, timezone

from ..agent.findings import AgentFinding
from .summary import count_fixes, severity_counts


def build_report(
    findings: list[AgentFinding],
    summary: str = "",
    scores: dict | None = None,
    pr_number: int = 0,
    repository: str = "",
    agent_mode: str = "single",
    model: str = "",
    provider: str = "",
    tools_used: list[str] | None = None,
    budget: dict | None = None,
    custom_rules: list | None = None,
) -> dict:
    categories: dict[str, int] = {}
    for finding in findings:
        categories[finding.category] = categories.get(finding.category, 0) + 1

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repository": repository,
        "pr_number": pr_number,
        "run": {
            "agent_mode": agent_mode,
            "provider": provider,
            "model": model,
            "tools_used": tools_used or [],
            **(budget or {}),
        },
        "summary": summary,
        "scores": scores or {},
        "totals": {
            "findings": len(findings),
            "by_severity": {k: v for k, v in severity_counts(findings).items() if v},
            "by_category": categories,
            "by_source": _by_source(findings),
            "fixes_applyable": count_fixes(findings)[0],
            "fixes_rejected": count_fixes(findings)[1],
        },
        # Adaptive mode: what the reviewer chose to check, and whether it fired.
        # Empty in other modes.
        "custom_rules": [r.to_dict() for r in (custom_rules or [])],
        "findings": [f.to_dict() for f in findings],
    }


def _by_source(findings: list[AgentFinding]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.source] = counts.get(finding.source, 0) + 1
    return counts


def write_report(report: dict, path: str) -> str:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    return path
