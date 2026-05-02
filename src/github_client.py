"""GitHub API interactions: fetch PR files, context, and post reviews."""

import logging
from fnmatch import fnmatch
from typing import Optional

from github import Github, GithubException
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

    Uses pull.get_files() (the PR's unified diff) and pull.head.sha for content
    fetching. This avoids 404s on newly added files whose intermediate commit
    SHAs may not have the file in their tree (e.g. after rebases or for files
    added partway through the PR's commit history).

    Returns a dict of {filename: {"sha": head_sha, "filename": filename}}.
    """
    changes: dict[str, dict] = {}
    head_sha = pull.head.sha

    for file in pull.get_files():
        if file.status in ("unchanged", "removed"):
            logger.info(f"Skipping {file.filename} (status: {file.status})")
            continue

        if not file.patch:
            logger.info(f"Skipping {file.filename} (no patch)")
            continue

        if any(fnmatch(file.filename.strip(), p.strip()) for p in patterns):
            changes[file.filename] = {
                "sha": head_sha,
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


def safe_create_review(
    pull: PullRequest,
    review_body: str,
    comments: list[dict],
) -> None:
    """Post a PR review, falling back to a summary if inline posting is rejected.

    GitHub's create_review API can return 422 with mixed errors when:
      - some paths cannot be resolved against the PR diff
      - too many comments are submitted at once ("was submitted too quickly")
    Rather than losing the entire review, we fall back to posting one
    consolidated review with all comment bodies inlined into the review_body
    (no per-line positioning). The LLM feedback is preserved.
    """
    if not comments:
        logger.info("No comments to post")
        return

    try:
        pull.create_review(
            body=review_body,
            event="COMMENT",
            comments=comments,
        )
        logger.info(f"Posted review with {len(comments)} inline comment(s)")
        return
    except GithubException as e:
        if e.status != 422:
            raise
        logger.warning(
            "Inline review rejected by GitHub (422). Falling back to a "
            "summary review without inline comments. Original error: %s",
            e.data,
        )

    summary_body = build_summary_review_body(review_body, comments)
    pull.create_review(
        body=summary_body,
        event="COMMENT",
    )
    logger.info(f"Posted summary review with {len(comments)} file section(s)")


def build_summary_review_body(review_body: str, comments: list[dict]) -> str:
    """Concatenate per-file comment bodies into one review-level markdown body.

    Used as the fallback payload when inline comments are rejected. The path
    used for the section header is taken from the comment's `path` field; if
    it has been URL-encoded, we unquote it for human readability.
    """
    from urllib.parse import unquote

    parts = [review_body, "", "---", ""]
    for c in comments:
        path = unquote(c.get("path", "unknown"))
        body = c.get("body", "")
        parts.append(f"### `{path}`")
        parts.append("")
        parts.append(body)
        parts.append("")
        parts.append("---")
        parts.append("")
    return "\n".join(parts)
