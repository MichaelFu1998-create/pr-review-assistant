"""Test coverage analysis — check if changed files have corresponding tests."""

import logging
import os

logger = logging.getLogger(__name__)

# Mapping of source patterns to test file patterns
TEST_PATTERNS = {
    ".py": [
        ("test_{name}.py", "tests/test_{name}.py", "test/{name}_test.py",
         "{dir}/tests/test_{name}.py", "{dir}/test_{name}.py"),
    ],
    ".js": [
        ("{name}.test.js", "{name}.spec.js",
         "__tests__/{name}.test.js", "tests/{name}.test.js"),
    ],
    ".jsx": [
        ("{name}.test.jsx", "{name}.spec.jsx",
         "__tests__/{name}.test.jsx"),
    ],
    ".ts": [
        ("{name}.test.ts", "{name}.spec.ts",
         "__tests__/{name}.test.ts", "tests/{name}.test.ts"),
    ],
    ".tsx": [
        ("{name}.test.tsx", "{name}.spec.tsx",
         "__tests__/{name}.test.tsx"),
    ],
    ".java": [
        ("{name}Test.java", "{dir}/test/{name}Test.java",
         "{name}Tests.java"),
    ],
    ".go": [
        ("{name}_test.go",),
    ],
    ".rb": [
        ("{name}_test.rb", "test/{name}_test.rb",
         "spec/{name}_spec.rb", "{name}_spec.rb"),
    ],
}


def analyze_test_coverage(
    changed_files: list[str], workspace: str
) -> list[str]:
    """Check if changed source files have corresponding test files.

    Returns a list of observations about test coverage.
    """
    source_files = []
    test_files_changed = []

    for filepath in changed_files:
        if _is_test_file(filepath):
            test_files_changed.append(filepath)
        elif _is_source_file(filepath):
            source_files.append(filepath)

    if not source_files:
        return []

    observations = []
    files_without_tests = []

    for source_file in source_files:
        if not _has_corresponding_test(source_file, workspace):
            files_without_tests.append(source_file)

    if files_without_tests:
        file_list = ", ".join(f"`{f}`" for f in files_without_tests[:5])
        extra = f" (and {len(files_without_tests) - 5} more)" if len(files_without_tests) > 5 else ""
        observations.append(
            f"No test files found for: {file_list}{extra}. "
            "Consider adding tests for new or modified code."
        )

    if source_files and not test_files_changed:
        observations.append(
            f"This PR modifies {len(source_files)} source file(s) but includes no test changes. "
            "Ensure adequate test coverage for the changes."
        )
    elif test_files_changed:
        ratio = len(test_files_changed) / len(source_files) if source_files else 0
        if ratio < 0.5:
            observations.append(
                f"This PR modifies {len(source_files)} source file(s) but only "
                f"{len(test_files_changed)} test file(s). Test coverage may be insufficient."
            )

    return observations


def _is_test_file(filepath: str) -> bool:
    """Check if a file is a test file based on naming conventions."""
    basename = os.path.basename(filepath).lower()
    return any([
        basename.startswith("test_"),
        basename.endswith(("_test.py", "_test.go", "_test.rb")),
        basename.endswith((".test.js", ".test.ts", ".test.jsx", ".test.tsx")),
        basename.endswith((".spec.js", ".spec.ts", ".spec.jsx", ".spec.tsx")),
        basename.endswith(("test.java", "tests.java")),
        "/test/" in filepath.lower(),
        "/tests/" in filepath.lower(),
        "/__tests__/" in filepath.lower(),
        "/spec/" in filepath.lower(),
    ])


def _is_source_file(filepath: str) -> bool:
    """Check if a file is a source code file (not config, docs, etc.)."""
    source_extensions = {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go",
        ".rb", ".rs", ".cs", ".cpp", ".c", ".kt", ".swift",
    }
    _, ext = os.path.splitext(filepath)
    return ext in source_extensions


def _has_corresponding_test(source_file: str, workspace: str) -> bool:
    """Check if a test file exists for the given source file."""
    basename = os.path.basename(source_file)
    dirname = os.path.dirname(source_file)
    name, ext = os.path.splitext(basename)

    patterns = TEST_PATTERNS.get(ext, [])
    for pattern_group in patterns:
        for pattern in pattern_group:
            test_path = pattern.format(name=name, dir=dirname)
            if os.path.exists(os.path.join(workspace, test_path)):
                return True

    return False
