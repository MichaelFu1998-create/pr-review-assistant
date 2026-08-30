"""Tests for structured findings: normalisation, collection, and merging."""

from src.agent.findings import (
    AgentFinding,
    FindingCollector,
    merge_findings,
    normalize_category,
    normalize_confidence,
    normalize_cwe,
    normalize_severity,
    _coerce_line,
)
from src.tools.base import Finding


class TestNormalisation:
    def test_severity_variants(self):
        assert normalize_severity("HIGH") == "high"
        assert normalize_severity("  Critical ") == "critical"
        assert normalize_severity("this is a medium issue") == "medium"

    def test_severity_falls_back_to_medium(self):
        assert normalize_severity(None) == "medium"
        assert normalize_severity("catastrophic") == "medium"

    def test_confidence_variants(self):
        assert normalize_confidence("High") == "high"
        assert normalize_confidence("low confidence") == "low"
        assert normalize_confidence("unsure") == "medium"

    def test_category_exact_and_fuzzy(self):
        assert normalize_category("security") == "security"
        assert normalize_category("API_Contract") == "api-contract"
        assert normalize_category("test") == "testing"

    def test_unknown_category_is_kept_not_dropped(self):
        assert normalize_category("vibes") == "correctness"
        assert normalize_category(None) == "correctness"

    def test_cwe_extraction(self):
        assert normalize_cwe("CWE-89") == "CWE-89"
        assert normalize_cwe("cwe 79") == "CWE-79"
        assert normalize_cwe("B608") is None
        assert normalize_cwe(None) is None

    def test_line_coercion(self):
        assert _coerce_line(42) == 42
        assert _coerce_line("42") == 42
        assert _coerce_line("line 42") == 42
        assert _coerce_line(0) is None
        assert _coerce_line(-1) is None
        assert _coerce_line(None) is None
        assert _coerce_line("nowhere") is None
        assert _coerce_line(True) is None  # bool is an int, but not a line


class TestAgentFinding:
    def test_from_tool_call_normalises_everything(self):
        f = AgentFinding.from_tool_call(
            {
                "path": " src/a.py ",
                "line": "12",
                "severity": "HIGH",
                "category": "Security",
                "cwe": "cwe 89",
                "title": " SQL injection ",
                "body": "Interpolated user input.",
                "confidence": "High",
                "evidence": "semgrep:sql-injection",
            }
        )
        assert (f.path, f.line, f.severity) == ("src/a.py", 12, "high")
        assert f.category == "security" and f.cwe == "CWE-89"
        assert f.title == "SQL injection"
        assert f.evidence == ["semgrep:sql-injection"]  # a bare string is wrapped

    def test_missing_optional_fields_get_defaults(self):
        f = AgentFinding.from_tool_call({"path": "a.py", "title": "x"})
        assert f.severity == "medium" and f.confidence == "medium"
        assert f.line is None and f.cwe is None and f.evidence == []

    def test_from_tool_finding_lifts_static_analysis(self):
        f = AgentFinding.from_tool_finding(
            Finding(
                file="a.py",
                line=3,
                severity="high",
                category="secret",
                rule_id="B105",
                message="hardcoded password",
                tool="bandit",
            )
        )
        assert f.path == "a.py" and f.line == 3
        assert f.category == "security"  # "secret" maps onto our taxonomy
        assert f.source == "bandit"
        assert f.evidence == ["bandit:B105"]

    def test_severity_rank_orders_correctly(self):
        critical = AgentFinding(path="a", title="t", severity="critical")
        low = AgentFinding(path="a", title="t", severity="low")
        assert critical.severity_rank < low.severity_rank

    def test_dedup_key_ignores_title_punctuation_and_case(self):
        a = AgentFinding(path="a.py", line=1, title="SQL Injection!")
        b = AgentFinding(path="a.py", line=1, title="sql   injection")
        assert a.dedup_key == b.dedup_key


class TestFindingCollector:
    def test_add_and_sort(self):
        c = FindingCollector()
        c.add(AgentFinding(path="a.py", title="low one", severity="low"))
        c.add(AgentFinding(path="a.py", title="critical one", severity="critical"))
        assert [f.title for f in c.sorted()] == ["critical one", "low one"]

    def test_sort_breaks_ties_by_confidence(self):
        c = FindingCollector()
        c.add(AgentFinding(path="a.py", title="unsure", severity="high", confidence="low"))
        c.add(AgentFinding(path="a.py", title="sure", severity="high", confidence="high"))
        assert [f.title for f in c.sorted()] == ["sure", "unsure"]

    def test_rejects_finding_without_path_or_title(self):
        c = FindingCollector()
        assert "path" in c.add(AgentFinding(path="", title="x"))
        assert "title" in c.add(AgentFinding(path="a.py", title=""))
        assert len(c) == 0

    def test_enforces_cap_and_says_so(self):
        c = FindingCollector(max_findings=2)
        for i in range(4):
            message = c.add(AgentFinding(path="a.py", title=f"t{i}"))
        assert len(c) == 2
        assert "limit" in message

    def test_add_returns_progress_message(self):
        c = FindingCollector()
        message = c.add(AgentFinding(path="a.py", line=7, title="x", severity="high"))
        assert "high" in message and "a.py:7" in message


class TestMergeFindings:
    def _tool_finding(self, line=10, message="issue", tool="semgrep"):
        return Finding(
            file="a.py",
            line=line,
            severity="high",
            category="security",
            rule_id="r1",
            message=message,
            tool=tool,
        )

    def test_tool_finding_at_agent_location_is_dropped(self):
        """The agent validated it, so its version — with the reasoning — wins."""
        agent = [AgentFinding(path="a.py", line=10, title="Confirmed SQL injection")]
        merged = merge_findings(agent, [self._tool_finding(line=10)])
        assert len(merged) == 1
        assert merged[0].source == "agent"

    def test_tool_finding_elsewhere_is_kept(self):
        agent = [AgentFinding(path="a.py", line=10, title="something")]
        merged = merge_findings(agent, [self._tool_finding(line=99)])
        assert len(merged) == 2
        assert {f.source for f in merged} == {"agent", "semgrep"}

    def test_duplicate_agent_findings_collapse(self):
        agent = [
            AgentFinding(path="a.py", line=1, title="Same thing"),
            AgentFinding(path="a.py", line=1, title="same  thing"),
        ]
        assert len(merge_findings(agent, [])) == 1

    def test_unlocated_tool_finding_survives(self):
        agent = [AgentFinding(path="a.py", line=10, title="x")]
        merged = merge_findings(agent, [self._tool_finding(line=None)])
        assert len(merged) == 2

    def test_empty_inputs(self):
        assert merge_findings([], []) == []


class TestCustomRuleFindings:
    """Hits from rules the reviewer authored read as its own findings, not as
    raw analyser output."""

    def _finding(self):
        from src.agent.findings import CUSTOM_RULE_SOURCE

        return Finding(
            file="app/exports.py", line=29, severity="high", category="security",
            rule_id="http-handler-missing-requires-scope",
            message="Handler missing @requires_scope; every other route has one",
            tool=CUSTOM_RULE_SOURCE,
        )

    def test_message_becomes_the_title_without_stuttering(self):
        f = AgentFinding.from_tool_finding(self._finding())
        assert f.title == "Handler missing @requires_scope; every other route has one"
        assert f.title not in f.body

    def test_body_names_the_rule(self):
        f = AgentFinding.from_tool_finding(self._finding())
        assert "http-handler-missing-requires-scope" in f.body
        assert "written for this repository" in f.body

    def test_confidence_is_high(self):
        """The reviewer wrote the rule deliberately; 'medium confidence' would
        misrepresent it."""
        assert AgentFinding.from_tool_finding(self._finding()).confidence == "high"

    def test_third_party_findings_are_unchanged(self):
        f = AgentFinding.from_tool_finding(Finding(
            file="a.py", line=1, severity="low", category="quality",
            rule_id="E501", message="line too long", tool="ruff",
        ))
        assert f.title == "E501: line too long"
        assert f.confidence == "medium"
        assert f.source == "ruff"
