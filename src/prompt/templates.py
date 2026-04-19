"""System message templates for different review personas."""

PERSONAS = {
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
    "strict": (
        "You are a strict code reviewer conducting a thorough security and quality audit. "
        "Flag all issues regardless of severity. Be direct and specific. Do not soften feedback. "
        "For each issue, state the category (security, quality, performance, maintainability) "
        "and severity (critical, high, medium, low).\n\n"
        "Respond in GitHub-flavored Markdown."
    ),
    "concise": (
        "You are a code reviewer. Provide brief, actionable feedback. Use bullet points. "
        "No explanations unless critical. Maximum 5 items per file. Focus on bugs, security "
        "issues, and major quality problems only.\n\n"
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


REVIEW_CHECKLIST = {
    "security": (
        "- Input validation and sanitization\n"
        "- Authentication and authorization checks\n"
        "- SQL injection, XSS, and other injection vulnerabilities\n"
        "- Sensitive data exposure (hardcoded secrets, logging PII)\n"
        "- Insecure cryptographic practices"
    ),
    "quality": (
        "- Code readability and naming conventions\n"
        "- Dead code and duplication\n"
        "- Error handling and edge cases\n"
        "- SOLID principles and design patterns\n"
        "- Code complexity and maintainability"
    ),
    "performance": (
        "- Algorithm efficiency and time complexity\n"
        "- Unnecessary computations or allocations\n"
        "- Database query optimization (N+1 queries)\n"
        "- Memory leaks and resource management\n"
        "- Caching opportunities"
    ),
    "education": (
        "- Best practices for the language/framework being used\n"
        "- Common beginner mistakes\n"
        "- Opportunities to learn standard library features\n"
        "- Testing practices and coverage\n"
        "- Documentation and code comments"
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
