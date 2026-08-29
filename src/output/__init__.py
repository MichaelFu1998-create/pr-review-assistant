"""Output surfaces derived from structured findings.

All of these are pure functions of the finding list, which is why inline
comments, SARIF, the JSON report, and severity gating cost no extra LLM tokens.
"""

from .comments import (
    build_inline_comments,
    encode_path,
    format_finding_body,
    is_agent_finding,
    split_by_source,
)
from .gating import should_fail, parse_fail_on
from .json_report import build_report, write_report
from .sarif import build_sarif, write_sarif
from .summary import build_review_body, count_fixes, format_severity_table, severity_counts

__all__ = [
    "build_inline_comments",
    "build_report",
    "build_review_body",
    "build_sarif",
    "count_fixes",
    "encode_path",
    "format_finding_body",
    "format_severity_table",
    "is_agent_finding",
    "split_by_source",
    "parse_fail_on",
    "severity_counts",
    "should_fail",
    "write_report",
    "write_sarif",
]
