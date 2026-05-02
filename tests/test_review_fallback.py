"""Tests for files_for_review() SHA selection and safe_create_review() fallback.

These cover two production failures observed in v1.1.1:
  1. 404 on get_contents() because the per-commit SHA recorded by the old
     files_for_review() pointed at a tree where the file did not exist (newly
     added files, force-pushed PRs).
  2. 422 from create_review() with mixed "Path could not be resolved" and
     "was submitted too quickly" errors causing the entire review to be lost.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from github import GithubException

from src.github_client import (
    files_for_review,
    safe_create_review,
    build_summary_review_body,
)


def _make_file(filename, status="modified", patch="@@ -1 +1 @@\n-old\n+new"):
    return SimpleNamespace(filename=filename, status=status, patch=patch)


def _make_pull(files, head_sha="HEAD_SHA"):
    pull = MagicMock()
    pull.head.sha = head_sha
    pull.get_files.return_value = files
    return pull


# --- files_for_review --------------------------------------------------------

def test_files_for_review_uses_head_sha_not_per_commit_sha():
    """All collected files must point at pull.head.sha so get_contents()
    resolves against a tree where they actually exist."""
    pull = _make_pull(
        [
            _make_file("backend/src/main/java/Foo.java", status="added"),
            _make_file("backend/src/main/java/Bar.java", status="modified"),
        ],
        head_sha="HEAD_SHA",
    )

    result = files_for_review(pull, ["*.java"])

    assert set(result.keys()) == {
        "backend/src/main/java/Foo.java",
        "backend/src/main/java/Bar.java",
    }
    for entry in result.values():
        assert entry["sha"] == "HEAD_SHA"


def test_files_for_review_skips_removed_and_unchanged():
    pull = _make_pull([
        _make_file("a.py", status="removed"),
        _make_file("b.py", status="unchanged"),
        _make_file("c.py", status="modified"),
    ])

    result = files_for_review(pull, ["*.py"])

    assert list(result.keys()) == ["c.py"]


def test_files_for_review_skips_files_with_no_patch():
    pull = _make_pull([
        _make_file("binary.png", status="added", patch=None),
        _make_file("code.py", status="added", patch="@@ -0,0 +1 @@\n+x"),
    ])

    result = files_for_review(pull, ["*"])

    assert list(result.keys()) == ["code.py"]


def test_files_for_review_applies_glob_pattern():
    pull = _make_pull([
        _make_file("src/Foo.java"),
        _make_file("README.md"),
        _make_file("src/Bar.java"),
    ])

    result = files_for_review(pull, ["*.java"])

    assert set(result.keys()) == {"src/Foo.java", "src/Bar.java"}


# --- safe_create_review ------------------------------------------------------

def _ghx(status, errors):
    """Build a GithubException matching what PyGithub raises."""
    return GithubException(
        status=status,
        data={"message": "Unprocessable Entity", "errors": errors},
        headers={},
    )


def test_safe_create_review_posts_inline_on_success():
    pull = MagicMock()
    comments = [{"path": "a.py", "position": 1, "body": "ok"}]

    safe_create_review(pull, "Review", comments)

    pull.create_review.assert_called_once_with(
        body="Review", event="COMMENT", comments=comments,
    )


def test_safe_create_review_falls_back_to_summary_on_422():
    """When inline posting fails with 422, post a single summary review
    with all comment bodies inlined — no per-line positioning."""
    pull = MagicMock()
    pull.create_review.side_effect = [
        _ghx(422, ["Path could not be resolved", "was submitted too quickly"]),
        None,  # summary call succeeds
    ]
    comments = [
        {"path": "a.py", "position": 1, "body": "issue in a"},
        {"path": "b.py", "position": 1, "body": "issue in b"},
    ]

    safe_create_review(pull, "Review header", comments)

    assert pull.create_review.call_count == 2
    fallback_call = pull.create_review.call_args_list[1]
    body = fallback_call.kwargs["body"]
    assert "issue in a" in body
    assert "issue in b" in body
    assert "a.py" in body
    assert "b.py" in body
    # No `comments` kwarg on the fallback (no inline positioning)
    assert "comments" not in fallback_call.kwargs


def test_safe_create_review_reraises_non_422():
    pull = MagicMock()
    pull.create_review.side_effect = _ghx(500, ["Internal Server Error"])

    with pytest.raises(GithubException):
        safe_create_review(pull, "x", [{"path": "a.py", "position": 1, "body": "y"}])


def test_safe_create_review_noop_on_empty_comments():
    pull = MagicMock()
    safe_create_review(pull, "x", [])
    pull.create_review.assert_not_called()


def test_summary_body_decodes_url_encoded_paths():
    """Bracket paths are URL-encoded for the inline API; the summary fallback
    should display them in human-readable form."""
    body = build_summary_review_body(
        "Review header",
        [{"path": "src/app/%5Bcountry%5D/page.tsx", "body": "issue"}],
    )
    assert "src/app/[country]/page.tsx" in body
    assert "%5B" not in body
