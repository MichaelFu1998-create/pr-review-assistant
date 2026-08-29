"""Severity gating: should this review fail the check run?

Off by default. A student blocked by a false positive learns to distrust the
tool, which costs more than the missed gate.
"""

import logging

from ..agent.findings import AgentFinding
from ..tools.base import SEVERITY_ORDER

logger = logging.getLogger(__name__)


def parse_fail_on(value: str) -> set[str]:
    """Parse the ``fail_on`` input into the set of severities that fail a run.

    Threshold semantics, keyed on the least severe level named: "high" and
    "critical,high" both mean critical-or-high, and "low" means everything.
    This matches the existing ``severity_threshold`` handling in
    ``tools/runner.py``, and it cannot under-fail — no configuration produces a
    gate that ignores critical findings while acting on lesser ones.
    """
    raw = [v.strip().lower() for v in (value or "").split(",") if v.strip()]
    if not raw:
        return set()

    known = [v for v in raw if v in SEVERITY_ORDER]
    unknown = [v for v in raw if v not in SEVERITY_ORDER]
    if unknown:
        logger.warning("Ignoring unknown fail_on severities: %s", ", ".join(unknown))
    if not known:
        return set()

    threshold = max(SEVERITY_ORDER[v] for v in known)
    return {s for s, rank in SEVERITY_ORDER.items() if rank <= threshold}


def should_fail(findings: list[AgentFinding], fail_on: str) -> tuple[bool, str]:
    """Return whether to exit non-zero, and why."""
    severities = parse_fail_on(fail_on)
    if not severities:
        return False, ""

    triggering = [f for f in findings if f.severity in severities]
    if not triggering:
        return False, ""

    counts: dict[str, int] = {}
    for finding in triggering:
        counts[finding.severity] = counts.get(finding.severity, 0) + 1
    detail = ", ".join(f"{n} {s}" for s, n in sorted(counts.items()))
    return True, f"fail_on={fail_on} matched {len(triggering)} finding(s): {detail}"
