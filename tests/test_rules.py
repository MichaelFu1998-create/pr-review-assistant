"""Agent-authored semgrep rules, and the validation that keeps them declarative.

The security claim of adaptive mode is that nothing the model writes is ever
executed as code. These tests are that claim.
"""

import os

import pytest
import yaml

from src.agent.rules import (
    MAX_YAML_BYTES,
    CustomRule,
    RuleCollector,
    rejection_feedback,
    validate_rule,
    write_rules_file,
)

VALID = """
rules:
  - id: route-missing-auth
    languages: [python]
    severity: ERROR
    message: Route has no @requires_scope
    patterns:
      - pattern: |
          @bp.route(...)
          def $F(...): ...
      - pattern-not: |
          @requires_scope(...)
          @bp.route(...)
          def $F(...): ...
"""

BARE = """
id: money-as-float
languages: [python]
severity: WARNING
message: Money must go through Money.from_cents
pattern: $X * 1.0
"""


def collector(max_rules=10):
    return RuleCollector(max_rules=max_rules)


def check(text, coll=None, **kw):
    kw.setdefault("run_semgrep_validate", False)
    return validate_rule(text, coll or collector(), **kw)


class TestAcceptance:
    def test_valid_rule_accepted(self):
        rule = check(VALID)
        assert rule.valid, rule.rejected_because
        assert rule.rule_id == "route-missing-auth"

    def test_bare_mapping_accepted(self):
        """A rule need not be wrapped in a 'rules:' list."""
        rule = check(BARE)
        assert rule.valid and rule.rule_id == "money-as-float"

    def test_all_severities(self):
        for sev in ("ERROR", "WARNING", "INFO", "error"):
            text = BARE.replace("severity: WARNING", f"severity: {sev}")
            assert check(text).valid, sev


class TestCodeExecutionIsRefused:
    """semgrep's pattern-where-python runs Python. It is gated behind
    --dangerously-allow-arbitrary-code-execution-from-rules, which we never
    pass — but a rule carrying it is refused outright."""

    def test_top_level_is_rejected(self):
        text = BARE + "\npattern-where-python: \"True\"\n"
        rule = check(text)
        assert not rule.valid
        assert "executes Python" in rule.rejected_because

    def test_nested_inside_patterns_is_rejected(self):
        text = """
rules:
  - id: sneaky-rule
    languages: [python]
    severity: ERROR
    message: nope
    patterns:
      - pattern: $X
      - pattern-where-python: "__import__('os').system('id')"
"""
        rule = check(text)
        assert not rule.valid
        assert "executes Python" in rule.rejected_because

    def test_deeply_nested_is_rejected(self):
        text = """
rules:
  - id: deep-rule
    languages: [python]
    severity: ERROR
    message: nope
    patterns:
      - pattern-either:
          - patterns:
              - pattern: $X
              - pattern-where-python: "True"
"""
        assert not check(text).valid

    def test_case_and_whitespace_variants_rejected(self):
        for key in ("Pattern-Where-Python", " pattern-where-python "):
            text = f"""
id: variant-rule
languages: [python]
severity: ERROR
message: nope
pattern: $X
"{key}": "True"
"""
            rule = check(text)
            assert not rule.valid, key

    def test_yaml_is_parsed_safely(self):
        """safe_load must not construct Python objects."""
        text = "!!python/object/apply:os.system ['echo pwned']"
        rule = check(text)
        assert not rule.valid


class TestRejections:
    def test_empty(self):
        assert "empty" in check("").rejected_because
        assert "empty" in check("   ").rejected_because

    def test_malformed_yaml(self):
        assert "invalid YAML" in check("id: [unclosed").rejected_because

    def test_not_a_mapping(self):
        assert not check("- just\n- a list").valid

    @pytest.mark.parametrize("missing", ["id", "message", "severity", "languages"])
    def test_missing_required_key(self, missing):
        body = yaml.safe_load(BARE)
        del body[missing]
        rule = check(yaml.safe_dump(body))
        assert not rule.valid
        assert missing in rule.rejected_because

    def test_rule_without_a_pattern_matches_nothing(self):
        body = yaml.safe_load(BARE)
        del body["pattern"]
        rule = check(yaml.safe_dump(body))
        assert "matches nothing" in rule.rejected_because

    def test_bad_rule_id(self):
        for bad in ("UPPER", "a", "has spaces", "under_score"):
            text = BARE.replace("money-as-float", bad)
            assert not check(text).valid, bad

    def test_duplicate_id(self):
        coll = collector()
        first = check(BARE, coll)
        coll.add(first)
        second = check(BARE, coll)
        assert not second.valid and "already used" in second.rejected_because

    def test_bad_severity(self):
        text = BARE.replace("severity: WARNING", "severity: CRITICAL")
        assert "severity must be" in check(text).rejected_because

    def test_languages_must_be_a_list(self):
        text = BARE.replace("languages: [python]", "languages: python")
        assert "non-empty list" in check(text).rejected_because

    def test_oversized_yaml(self):
        text = BARE + "\n# " + "x" * MAX_YAML_BYTES
        assert "larger than" in check(text).rejected_because

    def test_rule_cap_enforced(self):
        coll = collector(max_rules=2)
        for i in range(2):
            r = check(BARE.replace("money-as-float", f"rule-{i}"), coll)
            coll.add(r)
        third = check(BARE.replace("money-as-float", "rule-3"), coll)
        assert not third.valid and "limit" in third.rejected_because

    def test_multiple_rules_per_call_refused(self):
        body = yaml.safe_load(VALID)
        body["rules"].append(yaml.safe_load(BARE))
        rule = check(yaml.safe_dump(body))
        assert "exactly one rule" in rule.rejected_because


class TestFeedback:
    def test_names_the_reason_and_the_constraint(self):
        rule = check(BARE.replace("severity: WARNING", "severity: NOPE"))
        message = rejection_feedback(rule)
        assert "severity must be" in message
        assert "cannot run code" in message


class TestRulesFile:
    def test_writes_accepted_rules_only(self, tmp_path):
        accepted = check(VALID)
        rejected = CustomRule(rule_id="bad", yaml_text=BARE, rejected_because="nope")
        path = write_rules_file([accepted, rejected], str(tmp_path))

        doc = yaml.safe_load(open(path))
        assert [r["id"] for r in doc["rules"]] == ["route-missing-auth"]

    def test_none_when_nothing_accepted(self, tmp_path):
        assert write_rules_file([], str(tmp_path)) is None
        rejected = CustomRule(rule_id="x", yaml_text=BARE, rejected_because="no")
        assert write_rules_file([rejected], str(tmp_path)) is None

    def test_written_outside_the_checkout(self, tmp_path):
        """The action must not modify the repository it reviews."""
        path = write_rules_file([check(VALID)], str(tmp_path))
        assert path.startswith(str(tmp_path))

    def test_combines_several_rules(self, tmp_path):
        coll = collector()
        a = check(VALID, coll); coll.add(a)
        b = check(BARE, coll); coll.add(b)
        doc = yaml.safe_load(open(write_rules_file([a, b], str(tmp_path))))
        assert len(doc["rules"]) == 2


class TestCollector:
    def test_tracks_accepted_and_ids(self):
        coll = collector()
        coll.add(check(VALID, coll))
        coll.add(CustomRule(rule_id="x", yaml_text="", rejected_because="no"))
        assert len(coll.accepted) == 1
        assert coll.ids() == {"route-missing-auth"}

    def test_is_full(self):
        coll = collector(max_rules=1)
        assert not coll.is_full()
        coll.add(check(VALID, coll))
        assert coll.is_full()


@pytest.mark.skipif(
    not any(
        os.access(os.path.join(d, "semgrep"), os.X_OK)
        for d in os.environ.get("PATH", "").split(os.pathsep) if d
    ),
    reason="semgrep not installed",
)
class TestAgainstRealSemgrep:
    """The generated-file -> --config -> Finding path, end to end.

    Everything else mocks semgrep; this proves an authored rule actually fires.
    """

    RULE = """
rules:
  - id: route-missing-auth
    languages: [python]
    severity: ERROR
    message: Route has no @requires_scope
    patterns:
      - pattern: |
          @bp.route(...)
          def $F(...):
            ...
      - pattern-not: |
          @requires_scope(...)
          @bp.route(...)
          def $F(...):
            ...
"""

    def test_semgrep_validates_an_authored_rule(self):
        rule = validate_rule(self.RULE, collector(), run_semgrep_validate=True)
        assert rule.valid, rule.rejected_because

    def test_semgrep_rejects_a_rule_it_cannot_compile(self):
        """Valid YAML that semgrep itself refuses — the reason we shell out to
        --validate rather than trusting our own key checks."""
        broken = """
rules:
  - id: broken-regex
    languages: [python]
    severity: ERROR
    message: unbalanced group
    pattern-regex: "("
"""
        rule = validate_rule(broken, collector(), run_semgrep_validate=True)
        assert not rule.valid
        assert "semgrep rejected" in rule.rejected_because

    def test_authored_rule_fires_on_real_source(self, tmp_path):
        from src.tools.analyzers.semgrep import SemgrepTool
        from src.tools.runner import _run_single_tool

        (tmp_path / "app.py").write_text(
            "@requires_scope('admin')\n"
            "@bp.route('/safe')\n"
            "def safe():\n"
            "    return 1\n"
            "\n"
            "@bp.route('/unsafe')\n"
            "def unsafe():\n"
            "    return 2\n"
        )
        rule = validate_rule(self.RULE, collector(), run_semgrep_validate=False)
        path = write_rules_file([rule], str(tmp_path / "rules"))

        result = _run_single_tool(
            SemgrepTool(), ["app.py"], str(tmp_path), {"rulesets": [path]}
        )
        assert not result.errors, result.errors
        hits = [f for f in result.findings if f.rule_id.endswith("route-missing-auth")]
        assert len(hits) == 1, [f.rule_id for f in result.findings]
        # the decorated route must NOT match; only the bare one
        assert hits[0].line == 6
