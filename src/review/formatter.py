"""Format review output for GitHub PR comments."""

import logging

logger = logging.getLogger(__name__)


def format_review_comment(
    filename: str,
    llm_review: str,
    tool_summary: str | None = None,
    quality_observations: list[str] | None = None,
    test_observations: list[str] | None = None,
    hygiene_observations: list[str] | None = None,
) -> str:
    """Format a complete review comment for a single file."""
    parts = [f"### Review for `{filename}`\n"]

    # Quality observations (PR-level, shown once)
    if quality_observations:
        parts.append("#### PR Quality Notes")
        for obs in quality_observations:
            parts.append(f"- {obs}")
        parts.append("")

    # Test coverage observations
    if test_observations:
        parts.append("#### Test Coverage")
        for obs in test_observations:
            parts.append(f"- {obs}")
        parts.append("")

    # Git hygiene observations
    if hygiene_observations:
        parts.append("#### Git Hygiene")
        for obs in hygiene_observations:
            parts.append(f"- {obs}")
        parts.append("")

    # LLM review (the main content)
    parts.append(llm_review)

    return "\n".join(parts)


def format_review_body(
    file_count: int,
    tools_used: list[str],
    total_findings: int,
    persona: str,
) -> str:
    """Format the top-level review body."""
    parts = ["**Automated Code Review**\n"]

    parts.append(f"Reviewed **{file_count}** file(s)")

    if tools_used:
        tools_str = ", ".join(tools_used)
        parts.append(f" | Tools: {tools_str}")
        parts.append(f" | {total_findings} static analysis finding(s)")

    parts.append(f" | Mode: {persona}")

    return "".join(parts)
