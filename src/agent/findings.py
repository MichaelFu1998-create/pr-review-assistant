"""Structured review findings.

This is the keystone of v2. v1 produced markdown blobs, so nothing downstream
could anchor a comment, emit SARIF, score a PR, or deduplicate against the
static analysers. Findings here are records; every output surface is a pure
function of them, which is why adding SARIF/JSON/gating costs no extra tokens.

Models are loose about enum values ("HIGH", "Sec", "medium confidence"), so
everything arriving from a tool call is normalised rather than trusted.
"""

import logging
import re
from dataclasses import asdict, dataclass, field

from ..tools.base import SEVERITY_ORDER, Finding
from .fixes import Fix

logger = logging.getLogger(__name__)

SEVERITIES = ("critical", "high", "medium", "low", "info")
CONFIDENCES = ("high", "medium", "low")

# The review domains from the taxonomy. The agent is told to use these; anything
# unrecognised lands in "correctness" rather than being dropped, since a finding
# with an odd label is still a finding.
CATEGORIES = (
    "correctness",
    "security",
    "design",
    "testing",
    "performance",
    "api-contract",
    "operations",
    "documentation",
    "hygiene",
    "accessibility",
)

DEFAULT_CATEGORY = "correctness"

# Maps the categories the existing static-analysis tools emit onto ours.
TOOL_CATEGORY_MAP = {
    "security": "security",
    "secret": "security",
    "dependency": "security",
    "quality": "design",
}

_CWE_RE = re.compile(r"CWE[-_ ]?(\d+)", re.IGNORECASE)


def normalize_severity(value: str | None) -> str:
    text = (value or "").strip().lower()
    for severity in SEVERITIES:
        if severity in text:
            return severity
    return "medium"


def normalize_confidence(value: str | None) -> str:
    text = (value or "").strip().lower()
    for confidence in CONFIDENCES:
        if confidence in text:
            return confidence
    return "medium"


def normalize_category(value: str | None) -> str:
    text = (value or "").strip().lower().replace("_", "-").replace(" ", "-")
    if text in CATEGORIES:
        return text
    for category in CATEGORIES:
        if text and (text in category or category in text):
            return category
    return DEFAULT_CATEGORY


def normalize_cwe(value: str | None) -> str | None:
    if not value:
        return None
    match = _CWE_RE.search(str(value))
    return f"CWE-{match.group(1)}" if match else None


@dataclass
class AgentFinding:
    """One reviewable issue, from the agent or from a static analyser."""

    path: str
    title: str
    body: str = ""
    line: int | None = None
    end_line: int | None = None
    severity: str = "medium"
    category: str = DEFAULT_CATEGORY
    cwe: str | None = None
    confidence: str = "medium"
    evidence: list[str] = field(default_factory=list)
    suggested_fix: str | None = None
    # An exact, validated replacement rendered as a GitHub suggestion. Distinct
    # from suggested_fix, which is free text with no guaranteed line range.
    fix: Fix | None = None
    source: str = "agent"
    # Set by the reporter once the diff line map has resolved `line`; None means
    # the finding could not be anchored and belongs in the summary body.
    anchored_line: int | None = None

    @classmethod
    def from_tool_call(cls, arguments: dict, source: str = "agent") -> "AgentFinding":
        """Build from raw `post_finding` arguments, normalising as we go."""
        evidence = arguments.get("evidence") or []
        if isinstance(evidence, str):
            evidence = [evidence]

        fix = _fix_from_arguments(arguments)

        return cls(
            path=str(arguments.get("path") or "").strip(),
            title=str(arguments.get("title") or "").strip(),
            body=str(arguments.get("body") or "").strip(),
            line=_coerce_line(arguments.get("line")),
            end_line=_coerce_line(arguments.get("end_line")),
            severity=normalize_severity(arguments.get("severity")),
            category=normalize_category(arguments.get("category")),
            cwe=normalize_cwe(arguments.get("cwe")),
            confidence=normalize_confidence(arguments.get("confidence")),
            evidence=[str(e) for e in evidence][:10],
            suggested_fix=(arguments.get("suggested_fix") or None),
            fix=fix,
            source=source,
        )

    @classmethod
    def from_tool_finding(cls, finding: Finding) -> "AgentFinding":
        """Lift a static-analysis Finding into the same shape."""
        return cls(
            path=finding.file,
            title=f"{finding.rule_id}: {finding.message}"[:200] if finding.rule_id else finding.message[:200],
            body=finding.message,
            line=finding.line,
            severity=normalize_severity(finding.severity),
            category=TOOL_CATEGORY_MAP.get(finding.category, DEFAULT_CATEGORY),
            cwe=normalize_cwe(finding.rule_id),
            confidence="medium",
            evidence=[f"{finding.tool}:{finding.rule_id}"],
            suggested_fix=finding.suggestion,
            source=finding.tool,
        )

    @property
    def severity_rank(self) -> int:
        return SEVERITY_ORDER.get(self.severity, 4)

    @property
    def dedup_key(self) -> tuple:
        return (self.path, self.line, _normalize_title(self.title))

    def to_dict(self) -> dict:
        data = asdict(self)
        data["fix"] = self.fix.to_dict() if self.fix else None
        return data


def _fix_from_arguments(arguments: dict) -> Fix | None:
    """Build a Fix from post_finding's optional fix_* arguments.

    All three must be present and coherent; a partial fix is silently no fix,
    since the agent is told the requirement in the tool schema.
    """
    start = _coerce_line(arguments.get("fix_start_line"))
    end = _coerce_line(arguments.get("fix_end_line"))
    replacement = arguments.get("fix_replacement")

    if start is None or replacement is None:
        return None
    if not isinstance(replacement, str):
        return None
    # A single-line fix may omit the end line.
    if end is None:
        end = start
    return Fix(start_line=start, end_line=end, replacement=replacement.rstrip("\n"))


def _coerce_line(value) -> int | None:
    """Accept the several shapes a model uses for a line number."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    text = str(value).strip()
    match = re.search(r"\d+", text)
    if not match:
        return None
    line = int(match.group())
    return line if line > 0 else None


def _normalize_title(title: str) -> str:
    """Collapse a title to a comparable form for deduplication."""
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()[:80]


class FindingCollector:
    """Sink for `post_finding`."""

    def __init__(self, max_findings: int = 100):
        self._findings: list[AgentFinding] = []
        self.max_findings = max_findings
        self.rejected: list[str] = []

    def __len__(self) -> int:
        return len(self._findings)

    @property
    def findings(self) -> list[AgentFinding]:
        return list(self._findings)

    def add(self, finding: AgentFinding) -> str:
        """Record a finding. Returns the message shown back to the model."""
        if not finding.path:
            self.rejected.append("missing path")
            return "Rejected: 'path' is required."
        if not finding.title:
            self.rejected.append(f"missing title for {finding.path}")
            return "Rejected: 'title' is required."
        if len(self._findings) >= self.max_findings:
            self.rejected.append("collector full")
            return (
                f"Rejected: already at the {self.max_findings}-finding limit. "
                "Report only your most important remaining issues."
            )

        self._findings.append(finding)
        return (
            f"Recorded [{finding.severity}] {finding.category} finding on "
            f"{finding.path}:{finding.line or '?'} ({len(self._findings)} so far)."
        )

    def sorted(self) -> list[AgentFinding]:
        """Most severe first, then most confident, then by location."""
        confidence_rank = {"high": 0, "medium": 1, "low": 2}
        return sorted(
            self._findings,
            key=lambda f: (
                f.severity_rank,
                confidence_rank.get(f.confidence, 1),
                f.path,
                f.line if f.line is not None else 0,
            ),
        )


def merge_findings(
    agent_findings: list[AgentFinding],
    tool_findings: list[Finding],
) -> list[AgentFinding]:
    """Combine agent and static-analysis findings, dropping duplicates.

    The agent is prompted to validate tool output, so when both report the same
    place the agent's version wins: it carries the explanation and the
    false-positive judgement. Tool findings the agent never mentioned are kept,
    so a missed analyser hit still reaches the report.
    """
    merged: list[AgentFinding] = []
    seen: set[tuple] = set()

    for finding in agent_findings:
        if finding.dedup_key in seen:
            continue
        seen.add(finding.dedup_key)
        merged.append(finding)

    # Location match is enough for a tool finding: the wording will differ from
    # the agent's, so comparing titles would let every duplicate through.
    agent_locations = {(f.path, f.line) for f in agent_findings if f.line is not None}

    for finding in tool_findings:
        lifted = AgentFinding.from_tool_finding(finding)
        if lifted.line is not None and (lifted.path, lifted.line) in agent_locations:
            continue
        if lifted.dedup_key in seen:
            continue
        seen.add(lifted.dedup_key)
        merged.append(lifted)

    if len(merged) != len(agent_findings) + len(tool_findings):
        logger.info(
            "Merged %d agent + %d tool findings into %d",
            len(agent_findings),
            len(tool_findings),
            len(merged),
        )
    return merged
