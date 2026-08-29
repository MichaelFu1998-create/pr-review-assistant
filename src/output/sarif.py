"""SARIF 2.1.0 output, for GitHub code scanning.

Uploading this with github/codeql-action/upload-sarif puts every finding in the
repository's Security tab, with CWE tags and history across runs. It is derived
from the same findings as the inline comments, so it costs nothing extra.
"""

import hashlib
import json

from ..agent.findings import AgentFinding

SARIF_VERSION = "2.1.0"
SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"

# SARIF has four levels; our five severities fold onto them.
SEVERITY_TO_LEVEL = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "note",
    "info": "note",
}

# GitHub sorts and filters by security-severity (a CVSS-like 0-10 score).
SEVERITY_TO_SCORE = {
    "critical": "9.5",
    "high": "7.5",
    "medium": "5.0",
    "low": "3.0",
    "info": "1.0",
}


def rule_id(finding: AgentFinding) -> str:
    """A stable rule identifier, so alerts persist across runs.

    Prefer the CWE for security findings; otherwise derive from the category and
    a hash of the title, which keeps the same issue mapped to the same rule.
    """
    if finding.cwe:
        return finding.cwe
    digest = hashlib.sha256(finding.title.lower().encode()).hexdigest()[:8]
    return f"{finding.category}/{digest}"


def fingerprint(finding: AgentFinding) -> str:
    """Identify a finding across runs even when line numbers shift."""
    basis = f"{finding.path}|{finding.category}|{finding.title.lower()}"
    return hashlib.sha256(basis.encode()).hexdigest()[:16]


def build_sarif(
    findings: list[AgentFinding],
    tool_version: str = "2.0.0",
    information_uri: str = "https://github.com/MichaelFu1998-create/pr-review-assistant",
) -> dict:
    rules: dict[str, dict] = {}
    results = []

    for finding in findings:
        rid = rule_id(finding)
        if rid not in rules:
            properties = {
                "tags": ["pr-review", finding.category],
                "security-severity": SEVERITY_TO_SCORE.get(finding.severity, "5.0"),
            }
            if finding.cwe:
                properties["tags"].extend(["security", f"external/cwe/{finding.cwe.lower()}"])
            rules[rid] = {
                "id": rid,
                "name": finding.category.replace("-", "").title(),
                "shortDescription": {"text": finding.title[:120]},
                "fullDescription": {"text": (finding.body or finding.title)[:1000]},
                "defaultConfiguration": {
                    "level": SEVERITY_TO_LEVEL.get(finding.severity, "warning")
                },
                "properties": properties,
            }

        region = {"startLine": finding.line} if finding.line else {"startLine": 1}
        if finding.end_line and finding.line and finding.end_line >= finding.line:
            region["endLine"] = finding.end_line

        results.append(
            {
                "ruleId": rid,
                "ruleIndex": list(rules).index(rid),
                "level": SEVERITY_TO_LEVEL.get(finding.severity, "warning"),
                "message": {"text": _message(finding)},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {
                                "uri": finding.path,
                                "uriBaseId": "%SRCROOT%",
                            },
                            "region": region,
                        }
                    }
                ],
                "partialFingerprints": {"prReviewFingerprint/v1": fingerprint(finding)},
                "properties": {
                    "confidence": finding.confidence,
                    "source": finding.source,
                },
            }
        )

    return {
        "$schema": SARIF_SCHEMA,
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "pr-review-assistant",
                        "version": tool_version,
                        "informationUri": information_uri,
                        "rules": list(rules.values()),
                    }
                },
                "results": results,
            }
        ],
    }


def _message(finding: AgentFinding) -> str:
    text = finding.title
    if finding.body:
        text += f"\n\n{finding.body}"
    return text[:3000]


def write_sarif(findings: list[AgentFinding], path: str, tool_version: str = "2.0.0") -> str:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(build_sarif(findings, tool_version=tool_version), f, indent=2)
    return path
