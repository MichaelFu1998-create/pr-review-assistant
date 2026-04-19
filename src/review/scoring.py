"""Educational scoring rubric for PR reviews."""

import json
import logging
import re

logger = logging.getLogger(__name__)


def extract_scores(review_text: str) -> dict | None:
    """Attempt to extract scoring rubric from LLM review output.

    Returns dict like {"quality": 4, "security": 3, ...} or None if not found.
    """
    categories = ["code quality", "security", "testing", "documentation", "architecture"]
    scores = {}

    for category in categories:
        # Match patterns like "Code Quality (4/5)" or "Code Quality: 4/5" or "**Code Quality**: 4"
        pattern = rf"(?i){category}\D*(\d)\s*/?\s*5"
        match = re.search(pattern, review_text)
        if match:
            key = category.replace(" ", "_").lower()
            scores[key] = int(match.group(1))

    # Also try to find total
    total_match = re.search(r"(?i)total\D*(\d{1,2})\s*/?\s*25", review_text)
    if total_match:
        scores["total"] = int(total_match.group(1))

    return scores if scores else None


def format_score_summary(scores: dict) -> str:
    """Format a score summary as a markdown table."""
    if not scores:
        return ""

    lines = ["| Category | Score |", "|----------|-------|"]
    for key, value in scores.items():
        if key == "total":
            continue
        display_name = key.replace("_", " ").title()
        lines.append(f"| {display_name} | {value}/5 |")

    if "total" in scores:
        lines.append(f"| **Total** | **{scores['total']}/25** |")

    return "\n".join(lines)


def build_score_json(pr_number: int, scores: dict, tool_findings: dict) -> str:
    """Build a JSON summary for review history tracking."""
    return json.dumps({
        "pr_number": pr_number,
        "scores": scores,
        "tool_findings": tool_findings,
    }, indent=2)
