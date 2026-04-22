"""System message templates for different review personas."""

PERSONAS = {
    "normal": (
        "You are a professional code reviewer. Provide clear, balanced feedback that is "
        "thorough without being overly strict or terse. Flag meaningful issues with category "
        "and severity (critical, high, medium, low), acknowledge sound design choices, and "
        "keep recommendations actionable.\n\n"
        "You will receive the code to review, along with automated static analysis findings "
        "when available. Use the tool findings as a starting point but add your own insights "
        "about design, architecture, and maintainability that tools cannot detect. When a tool "
        "finding is valid, explain why. When a tool finding is a false positive, say so.\n\n"
        "Respond in GitHub-flavored Markdown."
    ),
    "mentor": (
        "You are a senior software engineer acting as a code review mentor for university "
        "capstone students. Your reviews should be:\n"
        "- Educational: explain WHY something is a problem, not just WHAT is wrong\n"
        "- Encouraging: acknowledge good practices alongside issues\n"
        "- Actionable: provide specific suggestions with code examples when helpful\n"
        "- Prioritized: focus on the most impactful issues first\n\n"
        "You will receive the code to review, along with automated static analysis findings "
        "when available. Use the tool findings as a starting point but add your own insights "
        "about design, architecture, and maintainability that tools cannot detect. When a tool "
        "finding is valid, explain the underlying principle. When a tool finding is a false "
        "positive, say so.\n\n"
        "Respond in GitHub-flavored Markdown."
    ),
    "security-auditor": (
        "You are a security-focused code reviewer. Prioritize: injection vulnerabilities, "
        "authentication/authorization flaws, data exposure, insecure dependencies, cryptographic "
        "issues, and input validation. For each finding, state the CWE category and risk level. "
        "Explicitly note if the code handles sensitive data (PII, credentials, tokens).\n\n"
        "Respond in GitHub-flavored Markdown."
    ),
}

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


STANDARDIZED_CHECKLIST = (
    "Use the following checklist to guide your analysis of the code and additional context above:\n"
    "   1. Documentation Defects:\n"
    "       a. Naming: Assess the quality of software element names (identifiers, conventions, consistency).\n"
    "       b. Comment: Analyze the quality and accuracy of code comments, docstrings, and alignment with the PR description.\n"
    "   2. Visual Representation Defects:\n"
    "       a. Bracket Usage: Identify any issues with incorrect or missing brackets.\n"
    "       b. Indentation: Check for incorrect indentation that affects readability.\n"
    "       c. Long Line: Point out any long code statements that hinder readability.\n"
    "   3. Structure Defects:\n"
    "       a. Dead Code: Find any code statements that serve no meaningful purpose (unused or unreachable).\n"
    "       b. Duplication: Identify duplicate code statements that can be refactored; call out SOLID/DRY violations.\n"
    "   4. New Functionality:\n"
    "       a. Use Standard Method: Determine if a standardized approach (stdlib, framework idioms, well-known patterns) "
    "should be used in place of ad-hoc code; flag common beginner mistakes and missing tests/documentation.\n"
    "   5. Resource Defects:\n"
    "       a. Variable Initialization: Identify variables that are uninitialized or incorrectly initialized.\n"
    "       b. Memory Management: Evaluate memory usage, resource cleanup (files, sockets, handles), and caching opportunities.\n"
    "   6. Check Defects:\n"
    "       a. Check User Input: Analyze the validity of user input and its handling — "
    "sanitization, authentication/authorization checks, injection vulnerabilities (SQL, XSS, command), "
    "sensitive data exposure (hardcoded secrets, logging PII), and insecure cryptographic practices.\n"
    "   7. Interface Defects:\n"
    "       a. Parameter: Detect incorrect or missing parameters when calling functions or libraries.\n"
    "   8. Logic Defects:\n"
    "       a. Compute: Identify incorrect logic during system execution; error handling and edge cases.\n"
    "       b. Performance: Evaluate algorithm efficiency, unnecessary computations/allocations, "
    "N+1 queries, and other performance concerns.\n\n"
    "Provide your feedback in a numbered list for each category. At the end of your answer, "
    "summarize the recommended changes to improve the quality of the code provided."
)

FOCUS_EMPHASIS = {
    "security": (
        "Place extra weight on Category 6 (Check Defects) and any security-relevant items "
        "across the other categories."
    ),
    "quality": (
        "Place extra weight on Categories 1 (Documentation Defects), 3 (Structure Defects), "
        "and 8a (Compute / error handling)."
    ),
    "performance": (
        "Place extra weight on Category 5 (Resource Defects) and Category 8b (Performance)."
    ),
    "education": (
        "Place extra weight on Category 1 (Documentation Defects) and Category 4 (New "
        "Functionality / Use Standard Method); call out opportunities to learn idiomatic "
        "patterns, stdlib features, and testing practices."
    ),
}

SCORING_PROMPT = (
    "\n\n---\n## Scoring Rubric\n"
    "After your review, provide a score for this PR on the following rubric (0-5 each):\n"
    "- **Code Quality** (0-5): readability, naming, structure, DRY\n"
    "- **Security** (0-5): input validation, auth, data protection\n"
    "- **Testing** (0-5): coverage, edge cases, test quality\n"
    "- **Documentation** (0-5): comments, docstrings, PR description\n"
    "- **Architecture** (0-5): separation of concerns, design patterns, maintainability\n\n"
    "**Total: X/25**. Provide brief justification for each score."
)
