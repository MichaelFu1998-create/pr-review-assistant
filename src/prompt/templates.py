"""Prompt templates for the v1 pipeline engine."""

REVIEWER_ROLE = (
    "You are a professional code reviewer. Provide clear, direct feedback: flag "
    "meaningful defects with a category and severity, acknowledge sound design "
    "choices, and keep every recommendation actionable.\n\n"
    "You will receive the code to review along with automated static analysis "
    "findings when available. Use the tool findings as a starting point, but add "
    "the insight about design, logic, and architecture that tools cannot reach. "
    "When a tool finding is valid, explain why it matters. When it is a false "
    "positive, say so plainly.\n\n"
    "Respond in GitHub-flavored Markdown."
)

# Extended language detection mapping
LANGUAGE_MAP = {
    "py": "Python",
    "js": "JavaScript",
    "jsx": "JavaScript (JSX)",
    "mjs": "JavaScript (ESM)",
    "ts": "TypeScript",
    "tsx": "TypeScript (TSX)",
    "java": "Java",
    "kt": "Kotlin",
    "kts": "Kotlin Script",
    "go": "Go",
    "rb": "Ruby",
    "rs": "Rust",
    "cs": "C#",
    "cpp": "C++",
    "cc": "C++",
    "c": "C",
    "h": "C/C++ Header",
    "hpp": "C++ Header",
    "swift": "Swift",
    "php": "PHP",
    "scala": "Scala",
    "r": "R",
    "R": "R",
    "dart": "Dart",
    "lua": "Lua",
    "sh": "Shell",
    "bash": "Bash",
    "zsh": "Zsh",
    "ps1": "PowerShell",
    "sql": "SQL",
    "tf": "Terraform (HCL)",
    "yml": "YAML",
    "yaml": "YAML",
    "json": "JSON",
    "xml": "XML",
    "html": "HTML",
    "css": "CSS",
    "scss": "SCSS",
    "less": "Less",
    "vue": "Vue",
    "svelte": "Svelte",
    "ex": "Elixir",
    "exs": "Elixir Script",
    "erl": "Erlang",
    "hs": "Haskell",
    "ml": "OCaml",
    "clj": "Clojure",
    "gradle": "Gradle",
}


def detect_language(filename: str) -> str | None:
    """Detect the programming language from a file extension."""
    parts = filename.rsplit(".", 1)
    if len(parts) < 2:
        # Handle special filenames
        basename = filename.rsplit("/", 1)[-1]
        special = {
            "Dockerfile": "Dockerfile",
            "Makefile": "Makefile",
            "Gemfile": "Ruby (Gemfile)",
            "Rakefile": "Ruby (Rakefile)",
        }
        return special.get(basename)
    return LANGUAGE_MAP.get(parts[1])


REPORTING_BAR = (
    "## The bar for reporting\n\n"
    "Report a finding only when you can state a **concrete failure**: the input "
    "or condition that triggers it, what goes wrong, and the consequence. If you "
    "cannot name the trigger, you are guessing — say nothing.\n\n"
    "A short review of real problems is worth far more than a long list of "
    "observations. Volume is not thoroughness."
)

DO_NOT_REPORT = (
    "## Do not report\n\n"
    "Formatters and linters run before you and own these entirely:\n"
    "- line length, bracket or brace placement, indentation, whitespace\n"
    "- import order, quote style, trailing commas\n"
    "- naming conventions, casing, abbreviation\n"
    "- \"add a comment here\" or \"add a docstring\" where nothing is misleading\n"
    "- subjective style preferences, or restating what the code plainly does\n\n"
    "Flag a name only when it is actively **misleading** about what the value "
    "holds or what the function does — not when it is merely terse. Raising a "
    "style nit costs you the author's attention for the finding that mattered."
)

STANDARDIZED_CHECKLIST = (
    "## What to review\n\n"
    "Work in this order. The question is always *would this cause harm, and can "
    "I say how* — not *does this match a convention*.\n\n"
    "1. **Correctness** — wrong results or crashes. Inverted conditions, "
    "off-by-one errors, unhandled None or error returns, swallowed exceptions, "
    "resource leaks, race conditions, encoding and timezone mistakes, floating "
    "point used for money. Trace the actual control flow rather than reading the "
    "code as prose.\n"
    "2. **Security** — injection (SQL/NoSQL, command, LDAP, template, XPath), "
    "XSS, missing authorisation on a new endpoint, IDOR, JWT misuse, SSRF, path "
    "traversal, unsafe deserialization, XXE, hardcoded secrets, weak or misused "
    "cryptography, vulnerable or unpinned dependencies, permissive infrastructure "
    "configuration, CI/CD script injection, PII in logs, and denial of service. "
    "Give the **CWE** and the realistic impact for every security finding.\n"
    "3. **Reliability and operations** — new network calls without timeouts or "
    "retries, unbounded retry loops, errors that vanish, a new failure path with "
    "no logging or metric, configuration that is required but undocumented.\n"
    "4. **Performance** — N+1 queries, a new filter or column without an index, "
    "unbounded queries or allocations, blocking I/O on an async path, repeated "
    "work inside a loop, regexes that can backtrack catastrophically. Only where "
    "it would matter at realistic scale; do not micro-optimise.\n"
    "5. **API and contract** — breaking changes to a public interface, unsafe "
    "migrations (adding a NOT NULL column, dropping one), versioning, "
    "idempotency, pagination.\n"
    "6. **Test adequacy** — is the new behaviour covered; are the error and edge "
    "paths tested, not just the happy one; do the tests assert real behaviour "
    "rather than a mock's return value; are they deterministic (no sleeps, live "
    "network, or unseeded clocks and randomness).\n"
    "7. **Design and maintainability** — only where it will cause demonstrable "
    "future pain: logic duplicated from a helper you can name, a function doing "
    "several unrelated jobs, an abstraction that leaks its implementation, dead "
    "code. Cite the evidence; do not assert it.\n"
    "8. **Documentation** — only where a comment, docstring, or the PR "
    "description is **misleading**, or documents a public contract incorrectly. "
    "The absence of a comment is not a finding.\n\n"
    "State a severity (critical, high, medium, low) for each finding, based on "
    "consequence rather than tidiness. Note what the change does well. End with "
    "the two or three things that most need attention."
)

FOCUS_EMPHASIS = {
    "security": (
        "Weight section 2 (Security) above everything else. Treat every input as "
        "attacker-controlled until you have traced otherwise."
    ),
    "quality": (
        "Weight sections 1 (Correctness), 3 (Reliability) and 7 (Design)."
    ),
    "performance": (
        "Weight section 4 (Performance), and resource handling in section 1."
    ),
}

SCORING_PROMPT = (
    "\n\n---\n## Scoring\n"
    "After your review, score this PR 0-5 in each category:\n"
    "- **Correctness** (0-5): does it do the right thing, including on error and edge paths\n"
    "- **Security** (0-5): input handling, authorisation, secrets, dependencies\n"
    "- **Testing** (0-5): coverage of new behaviour, edge cases, test quality\n"
    "- **Performance** (0-5): efficiency at realistic scale, resource handling\n"
    "- **Maintainability** (0-5): structure, coupling, clarity of intent\n\n"
    "**Total: X/25**. Justify each score in one line, citing something specific "
    "in the diff. A score without a reason is not useful to the author."
)
