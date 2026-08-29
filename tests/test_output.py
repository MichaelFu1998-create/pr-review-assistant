"""Tests for the output surfaces derived from structured findings."""

import json

from src.agent.findings import AgentFinding
from src.diff.patch import DiffMap
from src.output.comments import build_inline_comments, encode_path, format_finding_body
from src.output.gating import parse_fail_on, should_fail
from src.output.json_report import build_report
from src.output.sarif import build_sarif, fingerprint, rule_id
from src.output.summary import build_review_body, format_severity_table, severity_counts

PATCH = """@@ -1,4 +1,6 @@
 import os
+import pickle
+
 def load(raw):
     return raw
"""


def _diff(path="a.py"):
    return DiffMap.from_pull_files({path: {"patch": PATCH, "status": "modified"}})


def _finding(**kwargs):
    base = dict(
        path="a.py",
        line=2,
        severity="high",
        category="security",
        title="Unsafe deserialization",
        body="pickle.loads on untrusted input.",
        cwe="CWE-502",
        confidence="high",
        source="agent",
    )
    base.update(kwargs)
    return AgentFinding(**base)


class TestPathEncoding:
    """Next.js bracket routes 422 the inline-comment API unless encoded."""

    def test_brackets_encoded(self):
        assert (
            encode_path("src/app/[country]/[city]/page.tsx")
            == "src/app/%5Bcountry%5D/%5Bcity%5D/page.tsx"
        )

    def test_plain_path_untouched(self):
        assert encode_path("src/main.py") == "src/main.py"

    def test_slashes_preserved(self):
        assert encode_path("a/b/c.py").count("/") == 2

    def test_spaces_and_parens_encoded(self):
        encoded = encode_path("src/my file (copy).py")
        assert " " not in encoded and "(" not in encoded


class TestInlineComments:
    def test_anchored_finding_becomes_an_inline_comment(self):
        comments, unanchored = build_inline_comments([_finding(line=2)], _diff())
        assert unanchored == []
        assert len(comments) == 1
        assert comments[0]["line"] == 2
        assert comments[0]["side"] == "RIGHT"
        assert comments[0]["path"] == "a.py"

    def test_position_hack_is_gone(self):
        comments, _ = build_inline_comments([_finding(line=2)], _diff())
        assert "position" not in comments[0]

    def test_findings_on_one_line_merge_into_one_comment(self):
        comments, _ = build_inline_comments(
            [_finding(line=2, title="First"), _finding(line=2, title="Second")], _diff()
        )
        assert len(comments) == 1
        assert "First" in comments[0]["body"] and "Second" in comments[0]["body"]

    def test_unanchorable_finding_is_returned_not_dropped(self):
        comments, unanchored = build_inline_comments([_finding(line=9000)], _diff())
        assert comments == []
        assert len(unanchored) == 1

    def test_finding_on_a_file_outside_the_diff_is_unanchored(self):
        comments, unanchored = build_inline_comments(
            [_finding(path="other.py", line=2)], _diff()
        )
        assert comments == [] and len(unanchored) == 1

    def test_anchored_line_is_recorded_on_the_finding(self):
        finding = _finding(line=2)
        build_inline_comments([finding], _diff())
        assert finding.anchored_line == 2


class TestFindingBody:
    def test_includes_severity_category_and_cwe_link(self):
        body = format_finding_body(_finding())
        assert "High" in body and "security" in body
        assert "cwe.mitre.org/data/definitions/502.html" in body

    def test_low_confidence_is_surfaced(self):
        assert "low confidence" in format_finding_body(_finding(confidence="low"))

    def test_high_confidence_is_not_labelled(self):
        assert "confidence" not in format_finding_body(_finding(confidence="high"))

    def test_prose_advice_is_not_rendered_as_code(self):
        """A fence would make English advice look like an applyable fix that
        had somehow lost its button — the exact confusion this avoids."""
        body = format_finding_body(
            _finding(suggested_fix="Use PyJWT with an explicit algorithm allow-list.")
        )
        assert "> [!CAUTION]" in body
        assert "```" not in body
        assert "No Apply button" in body
        assert "beyond the lines commented" in body

    def test_evidence_and_source_in_footer(self):
        body = format_finding_body(_finding(source="bandit", evidence=["bandit:B301"]))
        assert "source: bandit" in body and "bandit:B301" in body


class TestSummary:
    def test_severity_table_counts(self):
        findings = [_finding(severity="critical"), _finding(severity="low"), _finding()]
        counts = severity_counts(findings)
        assert counts["critical"] == 1 and counts["high"] == 1 and counts["low"] == 1
        table = format_severity_table(findings)
        assert "Critical" in table and "medium" not in table.lower()

    def test_empty_findings_reads_clean(self):
        assert "No issues found" in format_severity_table([])

    def test_body_includes_summary_and_footer(self):
        body = build_review_body(
            [_finding()],
            summary="Adds a loader.",
            tools_used=["bandit"],
            budget={"steps": 7, "total_tokens": 12345, "elapsed_seconds": 3.2,
                    "stop_reason": "completed"},
            agent_mode="single",
            model="gpt-5.4-mini",
        )
        assert "Adds a loader." in body
        assert "7 steps" in body and "12,345 tokens" in body
        assert "single" in body

    def test_early_stop_is_called_out(self):
        body = build_review_body(
            [], summary="", budget={"steps": 25, "stop_reason": "step limit reached (25)"}
        )
        assert "stopped early" in body

    def test_unanchored_findings_appear_in_the_body(self):
        body = build_review_body([], summary="", unanchored=[_finding(path="other.py")])
        assert "Additional findings" in body and "other.py" in body

    def test_scores_render_as_a_table(self):
        body = build_review_body([], summary="", scores={"security": 3, "total": 18})
        assert "Scores" in body and "3/5" in body and "18/25" in body

    def test_observations_are_included(self):
        body = build_review_body(
            [], summary="", observations={"Test Coverage": ["No tests added."]}
        )
        assert "Test Coverage" in body and "No tests added." in body


class TestSarif:
    def test_document_shape(self):
        doc = build_sarif([_finding()])
        assert doc["version"] == "2.1.0"
        driver = doc["runs"][0]["tool"]["driver"]
        assert driver["name"] == "pr-review-assistant"
        assert len(doc["runs"][0]["results"]) == 1

    def test_severity_maps_to_sarif_levels(self):
        doc = build_sarif(
            [_finding(severity="critical"), _finding(severity="medium"),
             _finding(severity="low")]
        )
        assert [r["level"] for r in doc["runs"][0]["results"]] == ["error", "warning", "note"]

    def test_cwe_becomes_the_rule_id_and_a_tag(self):
        doc = build_sarif([_finding()])
        rule = doc["runs"][0]["tool"]["driver"]["rules"][0]
        assert rule["id"] == "CWE-502"
        assert "external/cwe/cwe-502" in rule["properties"]["tags"]

    def test_non_security_finding_gets_a_stable_derived_rule_id(self):
        finding = _finding(cwe=None, category="design", title="God function")
        assert rule_id(finding) == rule_id(_finding(cwe=None, category="design",
                                                    title="God function"))
        assert rule_id(finding).startswith("design/")

    def test_rule_index_points_at_the_right_rule(self):
        doc = build_sarif([_finding(cwe="CWE-89", title="A"), _finding(cwe="CWE-79", title="B")])
        rules = doc["runs"][0]["tool"]["driver"]["rules"]
        for result in doc["runs"][0]["results"]:
            assert rules[result["ruleIndex"]]["id"] == result["ruleId"]

    def test_fingerprint_is_stable_across_line_moves(self):
        assert fingerprint(_finding(line=2)) == fingerprint(_finding(line=99))

    def test_missing_line_defaults_to_one(self):
        doc = build_sarif([_finding(line=None)])
        region = doc["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["region"]
        assert region["startLine"] == 1

    def test_serialises(self):
        json.dumps(build_sarif([_finding()]))


class TestJsonReport:
    def test_report_shape_and_rollups(self):
        report = build_report(
            [_finding(), _finding(severity="low", category="design", source="ruff")],
            summary="s",
            pr_number=7,
            repository="o/r",
            model="gpt-5.4-mini",
            provider="openai",
            tools_used=["ruff"],
            budget={"steps": 4, "total_tokens": 900},
        )
        assert report["pr_number"] == 7 and report["repository"] == "o/r"
        assert report["totals"]["findings"] == 2
        assert report["totals"]["by_severity"] == {"high": 1, "low": 1}
        assert report["totals"]["by_category"] == {"security": 1, "design": 1}
        assert report["totals"]["by_source"] == {"agent": 1, "ruff": 1}
        assert report["run"]["steps"] == 4

    def test_findings_are_fully_serialised(self):
        report = build_report([_finding()])
        assert report["findings"][0]["cwe"] == "CWE-502"
        json.dumps(report)

    def test_empty_review(self):
        report = build_report([])
        assert report["totals"]["findings"] == 0


class TestGating:
    def test_disabled_by_default(self):
        assert parse_fail_on("") == set()
        assert should_fail([_finding(severity="critical")], "") == (False, "")

    def test_single_value_is_a_threshold(self):
        assert parse_fail_on("high") == {"critical", "high"}
        assert parse_fail_on("medium") == {"critical", "high", "medium"}

    def test_a_list_keys_on_its_least_severe_entry(self):
        """So no configuration can gate on 'low' while ignoring 'critical'."""
        assert parse_fail_on("critical,low") == {"critical", "high", "medium", "low"}
        assert parse_fail_on("critical,high") == {"critical", "high"}

    def test_unknown_severities_ignored(self):
        assert parse_fail_on("catastrophic") == set()
        assert parse_fail_on("high,bogus") == {"critical", "high"}

    def test_fails_and_explains(self):
        failed, reason = should_fail(
            [_finding(severity="critical"), _finding(severity="low")], "high"
        )
        assert failed and "1 critical" in reason

    def test_does_not_fail_below_threshold(self):
        failed, _ = should_fail([_finding(severity="low")], "high")
        assert failed is False


class TestSarifSchemaConstraints:
    """GitHub validates uploaded SARIF and rejects the entire file on any
    violation. Regression for testbed#5, where every security finding with a
    CWE emitted the "security" tag twice and the upload failed."""

    def test_tags_are_unique(self):
        doc = build_sarif([
            _finding(category="security", cwe="CWE-89"),
            _finding(category="security", cwe=None, title="No cwe"),
            _finding(category="design", cwe="CWE-208", title="Design with cwe"),
        ])
        for rule in doc["runs"][0]["tool"]["driver"]["rules"]:
            tags = rule["properties"]["tags"]
            assert len(tags) == len(set(tags)), f"duplicate tags in {rule['id']}: {tags}"

    def test_security_category_with_cwe_keeps_one_security_tag(self):
        doc = build_sarif([_finding(category="security", cwe="CWE-89")])
        tags = doc["runs"][0]["tool"]["driver"]["rules"][0]["properties"]["tags"]
        assert tags.count("security") == 1
        assert "external/cwe/cwe-89" in tags
        assert "pr-review" in tags

    def test_rule_ids_are_unique(self):
        """SARIF also requires unique rule ids within a run."""
        doc = build_sarif([_finding(cwe="CWE-89"), _finding(cwe="CWE-89", title="Other")])
        ids = [r["id"] for r in doc["runs"][0]["tool"]["driver"]["rules"]]
        assert len(ids) == len(set(ids))


def test_sarif_validates_against_the_official_schema(tmp_path):
    """Full-schema check, so constraints we have not thought of are caught here
    rather than by GitHub rejecting the upload.

    Skips when jsonschema or the network is unavailable; the targeted
    uniqueItems tests above still run unconditionally.
    """
    jsonschema = pytest.importorskip("jsonschema")
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(
            "https://json.schemastore.org/sarif-2.1.0.json", timeout=15
        ) as response:
            schema = json.loads(response.read())
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        pytest.skip(f"SARIF schema unavailable: {e}")

    doc = build_sarif([
        _finding(category="security", cwe="CWE-89"),
        _finding(category="correctness", cwe=None, title="No cwe", line=None),
        _finding(category="design", cwe="CWE-208", title="Design", severity="low"),
    ])
    errors = sorted(jsonschema.Draft7Validator(schema).iter_errors(doc), key=lambda e: e.path)
    assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors[:5])
