"""Git hygiene checks — large files, sensitive files, merge commits."""

import logging
import os

from github.PullRequest import PullRequest

logger = logging.getLogger(__name__)

SENSITIVE_PATTERNS = [
    ".env", ".env.local", ".env.production",
    "credentials.json", "service-account.json",
    "id_rsa", "id_ed25519",
    ".pem", ".key", ".p12", ".pfx",
    ".htpasswd", ".netrc",
    "secrets.yaml", "secrets.yml",
    "token.json",
]

LARGE_FILE_THRESHOLD = 1_000_000  # 1MB


def check_git_hygiene(
    pull: PullRequest,
    changed_files: dict[str, dict],
    workspace: str,
) -> list[str]:
    """Run git hygiene checks and return observations."""
    observations = []

    # Check for sensitive files
    sensitive_found = []
    for filepath in changed_files:
        basename = os.path.basename(filepath)
        for pattern in SENSITIVE_PATTERNS:
            if basename == pattern or basename.endswith(pattern):
                sensitive_found.append(filepath)
                break

    if sensitive_found:
        file_list = ", ".join(f"`{f}`" for f in sensitive_found)
        observations.append(
            f"**WARNING: Potentially sensitive files detected:** {file_list}. "
            "Ensure these files do not contain secrets, credentials, or private keys. "
            "Consider using .gitignore and environment variables instead."
        )

    # Check for large files
    large_files = []
    for filepath in changed_files:
        full_path = os.path.join(workspace, filepath)
        if os.path.exists(full_path):
            size = os.path.getsize(full_path)
            if size > LARGE_FILE_THRESHOLD:
                large_files.append((filepath, size))

    if large_files:
        for filepath, size in large_files:
            size_mb = size / 1_000_000
            observations.append(
                f"Large file detected: `{filepath}` ({size_mb:.1f}MB). "
                "Consider using Git LFS for large binary files."
            )

    # Check for merge commits
    commits = list(pull.get_commits())
    merge_commits = [c for c in commits if len(c.parents) > 1]
    if merge_commits:
        observations.append(
            f"{len(merge_commits)} merge commit(s) found in this PR. "
            "Consider rebasing instead of merging to keep a clean history."
        )

    # Check for WIP/fixup commits
    wip_commits = [
        c for c in commits
        if any(c.commit.message.lower().startswith(prefix)
               for prefix in ("wip", "fixup!", "squash!", "tmp", "temp"))
    ]
    if wip_commits:
        observations.append(
            f"{len(wip_commits)} WIP/temporary commit(s) found. "
            "Squash these before merging to keep a clean git history."
        )

    return observations
