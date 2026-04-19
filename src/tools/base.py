"""Abstract base class for static analysis tools."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Finding:
    """A single finding from a static analysis tool."""
    file: str
    line: int | None
    severity: str       # normalized: critical, high, medium, low, info
    category: str       # security, quality, dependency, secret
    rule_id: str        # e.g. "B301" or "no-eval"
    message: str
    tool: str
    suggestion: str | None = None


@dataclass
class ToolResult:
    """Result from running a static analysis tool."""
    tool_name: str
    findings: list[Finding] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    execution_time_ms: int = 0


# Severity ordering for sorting
SEVERITY_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}


class BaseTool(ABC):
    """Abstract base class for all static analysis tools.

    To add a new tool, create a file in analyzers/ that subclasses this
    and implements all abstract methods. The registry auto-discovers it.
    """
    name: str = ""
    languages: list[str] = []
    category: str = ""         # "security", "quality", "dependency", "secret"
    install_cmd: str | None = None

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the tool is installed and available."""
        ...

    @abstractmethod
    def install(self) -> bool:
        """Install the tool. Return True on success."""
        ...

    @abstractmethod
    def run(self, files: list[str], workspace: str, config: dict) -> ToolResult:
        """Run the tool on the given files and return findings."""
        ...

    def supports_language(self, language: str) -> bool:
        """Check if this tool supports the given language."""
        return language.lower() in [lang.lower() for lang in self.languages]

    def filter_files(self, files: list[str]) -> list[str]:
        """Filter files to only those this tool can analyze."""
        extensions = self._supported_extensions()
        if not extensions:
            return files  # Tool handles all files
        return [f for f in files if any(f.endswith(ext) for ext in extensions)]

    def _supported_extensions(self) -> list[str]:
        """Return file extensions this tool handles. Override for specificity."""
        lang_extensions = {
            "python": [".py"],
            "javascript": [".js", ".jsx", ".mjs"],
            "typescript": [".ts", ".tsx"],
            "java": [".java"],
            "go": [".go"],
            "ruby": [".rb"],
            "rust": [".rs"],
            "csharp": [".cs"],
            "cpp": [".cpp", ".cc", ".c", ".h", ".hpp"],
            "shell": [".sh", ".bash", ".zsh"],
            "dockerfile": ["Dockerfile"],
            "terraform": [".tf"],
            "kotlin": [".kt", ".kts"],
        }
        extensions = []
        for lang in self.languages:
            extensions.extend(lang_extensions.get(lang.lower(), []))
        return extensions


def format_findings_for_prompt(findings: list[Finding], max_findings: int = 50) -> str:
    """Format findings into a structured text block for the LLM prompt."""
    if not findings:
        return ""

    # Sort by severity
    sorted_findings = sorted(findings, key=lambda f: SEVERITY_ORDER.get(f.severity, 4))

    # Group by file
    by_file: dict[str, list[Finding]] = {}
    for f in sorted_findings:
        by_file.setdefault(f.file, []).append(f)

    lines = []
    total_shown = 0
    severity_counts = {"security": 0, "quality": 0, "dependency": 0, "secret": 0}

    for filename, file_findings in by_file.items():
        lines.append(f"### Findings for `{filename}`")
        for finding in file_findings:
            if total_shown >= max_findings:
                break
            line_info = f"Line {finding.line}" if finding.line else "General"
            lines.append(
                f"- **[{finding.severity.upper()}]** ({finding.tool}:{finding.rule_id}) "
                f"{line_info}: {finding.message}"
            )
            if finding.suggestion:
                lines.append(f"  - *Suggestion:* {finding.suggestion}")
            severity_counts[finding.category] = severity_counts.get(finding.category, 0) + 1
            total_shown += 1

    omitted = len(sorted_findings) - total_shown
    if omitted > 0:
        lines.append(f"\n*[{omitted} additional low-severity findings omitted]*")

    # Summary
    summary_parts = [f"{count} {cat}" for cat, count in severity_counts.items() if count > 0]
    if summary_parts:
        lines.append(f"\n**Summary:** {', '.join(summary_parts)} issues found")

    return "\n".join(lines)
