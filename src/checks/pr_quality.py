"""PR quality checks — description, commit messages, size."""

import logging
import re

from github.PullRequest import PullRequest

logger = logging.getLogger(__name__)


def check_pr_quality(pull: PullRequest, file_count: int) -> list[str]:
    """Run PR quality checks and return a list of observations.

    These are included in the prompt to give the LLM context about PR quality.
    """
    observations = []

    # PR description quality
    description = pull.body or ""
    if len(description.strip()) < 50:
        observations.append(
            "PR description is very short (< 50 characters). "
            "Good PR descriptions explain what changed and why."
        )

    # Check for linked issues
    if not re.search(r"(#\d+|fixes|closes|resolves)\s*#?\d+", description, re.IGNORECASE):
        observations.append(
            "No linked issues found in PR description. "
            "Consider referencing related issues (e.g., 'Fixes #123')."
        )

    # PR size
    if file_count > 15:
        observations.append(
            f"This PR changes {file_count} files, which is quite large. "
            "Consider breaking large PRs into smaller, focused changes."
        )
    elif file_count > 30:
        observations.append(
            f"This PR changes {file_count} files — very large PR. "
            "Large PRs are harder to review and more likely to contain issues."
        )

    # PR title quality
    title = pull.title or ""
    if len(title) < 10:
        observations.append("PR title is very short. Use a descriptive title.")
    if title.lower().startswith(("wip", "draft", "todo")):
        observations.append("PR title suggests this is work in progress.")

    # Commit message quality
    commits = list(pull.get_commits())
    short_msg_count = sum(1 for c in commits if len(c.commit.message.split("\n")[0]) < 10)
    if short_msg_count > 0:
        observations.append(
            f"{short_msg_count} commit(s) have very short messages (< 10 chars). "
            "Good commit messages describe what changed and why."
        )

    # Check for fixup/squash commits
    fixup_count = sum(
        1 for c in commits
        if c.commit.message.startswith(("fixup!", "squash!", "amend!"))
    )
    if fixup_count > 0:
        observations.append(
            f"{fixup_count} fixup/squash commit(s) found. "
            "Consider squashing these before merging."
        )

    return observations
