"""Specialist mandates for multi-agent mode.

One context covering ten review domains goes shallow on all of them — that is
v1's core weakness, and simply adding tools to a single prompt does not fix it.
Each specialist here gets the same toolbelt but a narrow mandate and its own
context window, so depth in one domain is not traded against breadth.

Selection is deterministic rather than a triage LLM call: file extensions and
paths already tell us whether a frontend or dependency reviewer has anything to
do, and spending a model call to learn that would cost tokens for no judgement.
"""

from dataclasses import dataclass
from typing import Callable

from .context import ReviewContext

FRONTEND_EXTENSIONS = (".jsx", ".tsx", ".vue", ".svelte", ".html", ".css", ".scss")
DEPENDENCY_FILES = (
    "requirements.txt", "pyproject.toml", "poetry.lock", "Pipfile", "Pipfile.lock",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "go.mod", "go.sum", "Gemfile", "Gemfile.lock", "pom.xml", "build.gradle",
    "Cargo.toml", "Cargo.lock", "composer.json", "composer.lock",
)
TEST_HINTS = ("test", "spec")
INFRA_HINTS = (".tf", ".yaml", ".yml", "Dockerfile", ".sh")


@dataclass
class Specialist:
    name: str
    mandate: str
    applies: Callable[[ReviewContext], bool]


def _any_path(context: ReviewContext, predicate: Callable[[str], bool]) -> bool:
    return any(predicate(p) for p in context.changed_paths)


def _always(_: ReviewContext) -> bool:
    return True


SPECIALISTS: list[Specialist] = [
    Specialist(
        name="security",
        mandate=(
            "You are the security reviewer. Work the security checklist above "
            "exhaustively and ignore style entirely. Trace untrusted input from "
            "where it enters to where it is used — read the surrounding files to "
            "do it, do not guess. For every finding give the CWE and the "
            "realistic impact. If a new endpoint, handler, or route appeared, "
            "your first question is who is allowed to call it, and your second "
            "is how you verified that."
        ),
        applies=_always,
    ),
    Specialist(
        name="correctness",
        mandate=(
            "You are the correctness reviewer. Hunt for bugs that ship: wrong "
            "conditions, off-by-one errors, unhandled None or error returns, "
            "swallowed exceptions, resource leaks, races, and encoding or "
            "timezone mistakes. Ignore style, security, and test coverage — "
            "other reviewers cover those. Trace the actual control flow rather "
            "than reading the code as prose."
        ),
        applies=_always,
    ),
    Specialist(
        name="testing",
        mandate=(
            "You are the test reviewer. Determine whether this change is "
            "adequately tested: does it add or update tests, do they cover the "
            "error and edge paths rather than only the happy one, do they assert "
            "real behaviour instead of a mock's return value, and are they "
            "deterministic (no sleeps, live network, or unseeded clocks and "
            "randomness). Search for the existing test files before concluding "
            "that none exist. Report untested new behaviour as a finding."
        ),
        applies=_always,
    ),
    Specialist(
        name="design",
        mandate=(
            "You are the design reviewer. Judge structure: coupling, functions "
            "doing several jobs, duplicated logic that should be shared, leaky "
            "abstractions, dead code, and breaking changes to a public "
            "interface. Before calling something duplicated, search for the "
            "existing helper and cite it. Ignore security and test coverage."
        ),
        applies=_always,
    ),
    Specialist(
        name="dependencies",
        mandate=(
            "You are the supply-chain reviewer. Examine every dependency this "
            "PR adds, removes, or bumps: is it maintained, is the version "
            "pinned, does the lockfile agree with the manifest, does the name "
            "resemble a more popular package, and does it run install hooks. "
            "Run a dependency analyser with run_analyzer. Report known "
            "vulnerabilities with their CVE."
        ),
        applies=lambda ctx: _any_path(
            ctx, lambda p: p.split("/")[-1] in DEPENDENCY_FILES
        ),
    ),
    Specialist(
        name="performance",
        mandate=(
            "You are the performance reviewer. Look for N+1 queries, a new "
            "column or filter without an index, unbounded queries or "
            "allocations, blocking I/O on an async path, repeated work inside a "
            "loop, and regexes that can backtrack catastrophically. Only report "
            "what would matter at realistic scale — do not micro-optimise."
        ),
        applies=_always,
    ),
    Specialist(
        name="infrastructure",
        mandate=(
            "You are the infrastructure and CI/CD reviewer. Examine Dockerfiles, "
            "Terraform, Kubernetes manifests, shell scripts, and GitHub Actions "
            "workflows for permissive IAM or CORS, open ingress, public storage, "
            "containers running as root, unpinned base images or action SHAs, "
            "missing resource limits, secrets reachable from forks, and script "
            "injection where ${{ github.event.* }} is interpolated into a run: "
            "block. Give the CWE where one applies."
        ),
        applies=lambda ctx: _any_path(
            ctx,
            lambda p: p.endswith(INFRA_HINTS)
            or p.split("/")[-1].startswith("Dockerfile")
            or ".github/workflows" in p,
        ),
    ),
    Specialist(
        name="frontend",
        mandate=(
            "You are the frontend reviewer. Cover accessibility (labels, alt "
            "text, keyboard navigation, focus management, colour contrast in "
            "new styles), internationalisation (hardcoded user-facing strings), "
            "and DOM-based XSS such as innerHTML or dangerouslySetInnerHTML with "
            "unsanitised input."
        ),
        applies=lambda ctx: _any_path(ctx, lambda p: p.endswith(FRONTEND_EXTENSIONS)),
    ),
]

SPECIALIST_NAMES = [s.name for s in SPECIALISTS]


def select_specialists(
    context: ReviewContext, requested: list[str] | None = None
) -> list[Specialist]:
    """Choose which specialists to run.

    An explicit list is honoured as given; otherwise each specialist decides
    from the changed paths whether it has anything to review.
    """
    if requested:
        wanted = {name.strip().lower() for name in requested}
        return [s for s in SPECIALISTS if s.name in wanted]
    return [s for s in SPECIALISTS if s.applies(context)]
