"""Tests for URL encoding of file paths in review comment construction.

Next.js 14 dynamic routes use square brackets in folder names (e.g.
src/app/[country]/[city]/page.tsx). GitHub's review API rejects literal
[ and ] in the comment `path` field with a 422 "Path could not be resolved"
error. These tests verify that quote(filename, safe="/") fixes the issue.
"""

from urllib.parse import quote


def test_bracket_path_is_encoded():
    filename = "src/app/[country]/[city]/page.tsx"
    encoded = quote(filename, safe="/")
    assert encoded == "src/app/%5Bcountry%5D/%5Bcity%5D/page.tsx"


def test_plain_path_is_unchanged():
    filename = "src/components/Button.tsx"
    encoded = quote(filename, safe="/")
    assert encoded == filename


def test_nested_brackets_all_encoded():
    filename = "app/[lang]/[...slug]/route.ts"
    encoded = quote(filename, safe="/")
    assert "[" not in encoded
    assert "]" not in encoded
    assert encoded == "app/%5Blang%5D/%5B...slug%5D/route.ts"


def test_comment_path_encoded_but_body_is_raw():
    """The API path field is encoded; the comment body keeps the human-readable name."""
    filenames = [
        "src/app/[country]/[city]/page.tsx",
        "src/components/Header.tsx",
    ]

    comments = []
    for filename in filenames:
        body = f"Review of {filename}"
        comments.append({
            "path": quote(filename, safe="/"),
            "position": 1,
            "body": body,
        })

    # Body keeps the raw, human-readable path
    assert "src/app/[country]/[city]/page.tsx" in comments[0]["body"]

    # API path has brackets encoded
    assert comments[0]["path"] == "src/app/%5Bcountry%5D/%5Bcity%5D/page.tsx"

    # Plain paths are passed through unchanged
    assert comments[1]["path"] == "src/components/Header.tsx"

    # No raw brackets in any path sent to the API
    for c in comments:
        assert "[" not in c["path"]
        assert "]" not in c["path"]


def test_parentheses_in_path_are_encoded():
    filename = "src/app/(marketing)/about/page.tsx"
    encoded = quote(filename, safe="/")
    assert "(" not in encoded
    assert ")" not in encoded


def test_spaces_in_path_are_encoded():
    filename = "src/components/My Component/index.tsx"
    encoded = quote(filename, safe="/")
    assert " " not in encoded
