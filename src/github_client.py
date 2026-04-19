"""GitHub API interactions: fetch PR files, context, and post reviews."""

import logging
from fnmatch import fnmatch
from typing import Optional

from github import Github
from github.PullRequest import PullRequest
from github.Commit import Commit
from github.Repository import Repository

logger = logging.getLogger(__name__)


def get_repo_and_pull(github_token: str, repo_name: str, pr_id: int) -> tuple[Repository, PullRequest]:
    """Initialize GitHub client and return repo + pull request objects."""
    g = Github(github_token)
    repo = g.get_repo(repo_name)
    pull = repo.get_pull(pr_id)
    return repo, pull


def is_merge_commit(commit: Commit) -> bool:
    return len(commit.parents) > 1


def files_for_review(pull: PullRequest, patterns: list[str]) -> dict[str, dict]:
    """Collect files changed in the PR that match the given glob patterns.

    Returns a dict of {filename: {"sha": commit_sha, "filename": filename}}.
    """
    changes: dict[str, dict] = {}
    commits = pull.get_commits()

    for commit in commits:
        if is_merge_commit(commit):
            logger.info(f"Skipping merge commit {commit.sha}")
            continue

        for file in commit.files:
            if file.status in ("unchanged", "removed"):
                logger.info(f"Skipping {file.filename} (status: {file.status})")
                continue

            if file.status == "renamed":
                logger.info(f"Skipping renamed file {file.filename}")
                if file.previous_filename in changes:
                    changes[file.filename] = changes.pop(file.previous_filename)
                continue

            if not file.patch:
                logger.info(f"Skipping {file.filename} (no patch)")
                continue

            if any(fnmatch(file.filename.strip(), p.strip()) for p in patterns):
                changes[file.filename] = {
                    "sha": commit.sha,
                    "filename": file.filename,
                }
                logger.info(f"Adding {file.filename} for review")

    return changes


def fetch_contextual_info(pull: PullRequest, repo: Repository) -> tuple[str, list[str], str]:
    """Fetch PR description, human comments, and README content."""
    pr_description = pull.body or "No description provided."

    comments = [
        comment.body
        for comment in list(pull.get_issue_comments()) + list(pull.get_review_comments())
        if not comment.user.type == "Bot"
    ]

    readme_content = "No README file found."
    for filename in ["README.md", "Readme.md", "readme.md", "README.MD", "ReadMe.md"]:
        try:
            readme_content = repo.get_contents(filename).decoded_content.decode("utf8")
            break
        except Exception:
            continue

    return pr_description, comments, readme_content


def get_file_content(repo: Repository, filename: str, commit_sha: str) -> Optional[str]:
    """Get decoded file content at a specific commit SHA."""
    try:
        content = repo.get_contents(filename, commit_sha).decoded_content.decode("utf8")
        return content if content else None
    except Exception as e:
        logger.warning(f"Failed to get content for {filename}@{commit_sha}: {e}")
        return None


def post_review(pull: PullRequest, comments: list[dict]) -> None:
    """Post a review with all comments to the PR."""
    if not comments:
        logger.info("No comments to post")
        return

    pull.create_review(
        body="**Automated Code Review**",
        event="COMMENT",
        comments=comments,
    )
    logger.info(f"Posted review with {len(comments)} comments")
