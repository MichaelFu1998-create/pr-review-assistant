"""The tools the review agent can call.

Everything here is read-only. The agent inspects the checkout, runs analysers,
and reports findings; it never writes to the repository. Tool *arguments* come
from model output and are therefore untrusted: paths go through
``ReviewContext.resolve`` and every subprocess is invoked with an argument list,
never a shell string.
"""

import logging
import subprocess
from dataclasses import dataclass

from ..llm.base import ToolCall, ToolSchema
from ..tools.base import Finding
from ..tools.registry import discover_tools
from ..tools.runner import _run_single_tool
from .budget import Budget
from .context import ReviewContext
from .findings import AgentFinding, FindingCollector

logger = logging.getLogger(__name__)

SUBPROCESS_TIMEOUT = 120
MAX_SEARCH_RESULTS = 100
MAX_OUTPUT_CHARS = 20_000


@dataclass
class ToolOutcome:
    """Result of one tool call, handed back to the model as a tool message."""

    text: str
    is_finish: bool = False
    payload: dict | None = None


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [output truncated at {limit} characters]"


class Toolbelt:
    def __init__(
        self,
        context: ReviewContext,
        collector: FindingCollector,
        budget: Budget,
        source: str = "agent",
    ):
        self.context = context
        self.collector = collector
        self.budget = budget
        # Labels each finding with what produced it: "agent" or an analyser name.
        self.source = source

    # --- schemas ---

    def schemas(self) -> list[ToolSchema]:
        return [
            ToolSchema(
                name="list_changed_files",
                description=(
                    "List the files changed in this pull request, with line counts, "
                    "status, and how many static-analysis findings each already has."
                ),
                parameters={"type": "object", "properties": {}},
            ),
            ToolSchema(
                name="read_diff",
                description=(
                    "Read the unified diff for one changed file, annotated with "
                    "new-file line numbers. Use these line numbers when reporting "
                    "findings. Start here: it shows what the PR actually changed."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Repo-relative path."}
                    },
                    "required": ["path"],
                },
            ),
            ToolSchema(
                name="read_file",
                description=(
                    "Read a file from the checkout with line numbers. Use this for "
                    "context around a diff, or to inspect a file the PR did not "
                    "change (a caller, a config, a test)."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "start_line": {"type": "integer"},
                        "end_line": {"type": "integer"},
                    },
                    "required": ["path"],
                },
            ),
            ToolSchema(
                name="search_repo",
                description=(
                    "Search tracked files for a regular expression. Use it to find "
                    "callers of a changed function, other uses of a risky pattern, "
                    "or whether a helper already exists."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "description": "Regular expression."},
                        "glob": {
                            "type": "string",
                            "description": "Optional path filter, e.g. '*.py'.",
                        },
                        "max_results": {"type": "integer"},
                    },
                    "required": ["pattern"],
                },
            ),
            ToolSchema(
                name="find_symbol",
                description=(
                    "Find where a function, class, or constant is defined and used. "
                    "Use it to check whether a changed signature has other callers."
                ),
                parameters={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            ),
            ToolSchema(
                name="list_analyzers",
                description=(
                    "List the static-analysis tools available to run on demand, and "
                    "what each detects."
                ),
                parameters={"type": "object", "properties": {}},
            ),
            ToolSchema(
                name="run_analyzer",
                description=(
                    "Run one static analyser on specific files. The standard "
                    "analysers already ran before you started; use this for a "
                    "targeted follow-up, such as a security scanner on a file you "
                    "suspect, or a tool that does not run by default."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "tool": {"type": "string", "description": "Analyser name."},
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Repo-relative paths to analyse.",
                        },
                    },
                    "required": ["tool", "paths"],
                },
            ),
            ToolSchema(
                name="git_log",
                description=(
                    "Recent commit history for a file. Use it to see whether an area "
                    "changes often or was recently fixed."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "count": {"type": "integer"},
                    },
                    "required": ["path"],
                },
            ),
            ToolSchema(
                name="read_pr_metadata",
                description=(
                    "The PR title, description, author, labels, and human comments. "
                    "Use it to check whether the diff does what the PR claims."
                ),
                parameters={"type": "object", "properties": {}},
            ),
            ToolSchema(
                name="post_finding",
                description=(
                    "Report one issue. Call this once per distinct issue, as you "
                    "find them. Cite a line number from read_diff so the comment "
                    "lands in the right place. Prefer a few well-evidenced findings "
                    "over many speculative ones."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Repo-relative path."},
                        "line": {
                            "type": "integer",
                            "description": "New-file line number, from read_diff.",
                        },
                        "end_line": {"type": "integer"},
                        "severity": {
                            "type": "string",
                            "enum": ["critical", "high", "medium", "low", "info"],
                        },
                        "category": {
                            "type": "string",
                            "enum": [
                                "correctness",
                                "security",
                                "design",
                                "testing",
                                "performance",
                                "api-contract",
                                "operations",
                                "documentation",
                                "hygiene",
                                "accessibility",
                            ],
                        },
                        "cwe": {
                            "type": "string",
                            "description": "For security findings, e.g. 'CWE-89'.",
                        },
                        "title": {
                            "type": "string",
                            "description": "One line naming the problem.",
                        },
                        "body": {
                            "type": "string",
                            "description": (
                                "Markdown: why it matters and how to fix it. Explain "
                                "the underlying principle, not just the symptom."
                            ),
                        },
                        "confidence": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                            "description": (
                                "How sure you are this is a real problem. Be honest; "
                                "low-confidence findings are reported as such."
                            ),
                        },
                        "evidence": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "What you checked: tool ids, file:line refs.",
                        },
                        "suggested_fix": {
                            "type": "string",
                            "description": "Optional corrected code.",
                        },
                    },
                    "required": ["path", "severity", "category", "title", "body"],
                },
            ),
            ToolSchema(
                name="finish",
                description=(
                    "End the review. Call this once you have investigated the "
                    "changes and reported every issue worth raising."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": (
                                "Markdown summary of the change and your overall "
                                "assessment. Note what is done well, not only faults."
                            ),
                        },
                        "scores": {
                            "type": "object",
                            "description": "Optional 0-5 scores.",
                            "properties": {
                                "code_quality": {"type": "integer"},
                                "security": {"type": "integer"},
                                "testing": {"type": "integer"},
                                "documentation": {"type": "integer"},
                                "architecture": {"type": "integer"},
                            },
                        },
                    },
                    "required": ["summary"],
                },
            ),
        ]

    # --- dispatch ---

    def dispatch(self, call: ToolCall) -> ToolOutcome:
        if call.parse_error:
            return ToolOutcome(
                f"Error: arguments were not valid JSON ({call.parse_error}). "
                "Retry with a well-formed JSON object."
            )

        handler = getattr(self, f"_tool_{call.name}", None)
        if handler is None:
            known = ", ".join(s.name for s in self.schemas())
            return ToolOutcome(f"Error: unknown tool '{call.name}'. Available: {known}")

        try:
            return handler(call.arguments)
        except Exception as e:  # a broken tool must not end the review
            logger.warning("Tool %s raised: %s", call.name, e)
            return ToolOutcome(f"Error running {call.name}: {e}")

    # --- handlers ---

    def _tool_list_changed_files(self, args: dict) -> ToolOutcome:
        return ToolOutcome(self.context.manifest())

    def _tool_read_diff(self, args: dict) -> ToolOutcome:
        path = str(args.get("path", ""))
        return ToolOutcome(_truncate(self.context.diff.read_diff(path)))

    def _tool_read_file(self, args: dict) -> ToolOutcome:
        return ToolOutcome(
            _truncate(
                self.context.read_file(
                    str(args.get("path", "")),
                    start=args.get("start_line"),
                    end=args.get("end_line"),
                )
            )
        )

    def _tool_search_repo(self, args: dict) -> ToolOutcome:
        pattern = str(args.get("pattern", "")).strip()
        if not pattern:
            return ToolOutcome("Error: 'pattern' is required.")

        limit = min(int(args.get("max_results") or 40), MAX_SEARCH_RESULTS)
        # git grep searches tracked files only and honours .gitignore, which is
        # exactly the scope a code review cares about.
        cmd = ["git", "grep", "-n", "-I", "--no-color", "-E", pattern]
        glob = args.get("glob")
        if glob:
            cmd.extend(["--", str(glob)])

        result = self._git(cmd)
        if result is None:
            return ToolOutcome("Error: search failed (is the workspace a git checkout?)")
        if not result.strip():
            return ToolOutcome(f"No matches for /{pattern}/.")

        lines = result.strip().split("\n")
        shown = lines[:limit]
        out = "\n".join(shown)
        if len(lines) > limit:
            out += f"\n... [{len(lines) - limit} more matches; narrow the pattern]"
        return ToolOutcome(_truncate(out))

    def _tool_find_symbol(self, args: dict) -> ToolOutcome:
        name = str(args.get("name", "")).strip()
        if not name:
            return ToolOutcome("Error: 'name' is required.")

        escaped = _escape_regex(name)
        definition = self._git(
            [
                "git", "grep", "-n", "-I", "--no-color", "-E",
                rf"(def|class|function|func|const|let|var|type|interface|struct)\s+{escaped}\b",
            ]
        )
        references = self._git(
            ["git", "grep", "-n", "-I", "--no-color", "-w", "-E", escaped]
        )

        parts = []
        if definition and definition.strip():
            parts.append("## Definitions\n" + "\n".join(definition.strip().split("\n")[:20]))
        else:
            parts.append("## Definitions\n(none found)")
        if references and references.strip():
            ref_lines = references.strip().split("\n")
            parts.append(
                f"## References ({len(ref_lines)} total, first 30)\n"
                + "\n".join(ref_lines[:30])
            )
        else:
            parts.append("## References\n(none found)")
        return ToolOutcome(_truncate("\n\n".join(parts)))

    def _tool_list_analyzers(self, args: dict) -> ToolOutcome:
        registry = discover_tools()
        already = ", ".join(self.context.tools_used) or "none"
        rows = [
            f"- `{name}`: {cls.category or 'general'} "
            f"({', '.join(cls.languages) if cls.languages else 'any language'})"
            for name, cls in sorted(registry.items())
        ]
        return ToolOutcome(
            f"Already run in the pre-pass: {already}\n\nAvailable:\n" + "\n".join(rows)
        )

    def _tool_run_analyzer(self, args: dict) -> ToolOutcome:
        name = str(args.get("tool", "")).strip()
        paths = args.get("paths") or []
        if isinstance(paths, str):
            paths = [paths]
        paths = [str(p) for p in paths]

        registry = discover_tools()
        tool_class = registry.get(name)
        if tool_class is None:
            return ToolOutcome(
                f"Error: unknown analyser '{name}'. Available: {', '.join(sorted(registry))}"
            )

        # Only analyse paths that resolve inside the checkout.
        safe_paths = [p for p in paths if self.context.resolve(p)]
        if not safe_paths:
            return ToolOutcome("Error: no valid paths inside the repository.")

        tool = tool_class()
        relevant = tool.filter_files(safe_paths)
        if not relevant:
            return ToolOutcome(
                f"{name} does not handle any of those file types "
                f"(it supports: {', '.join(tool.languages) or 'all'})."
            )

        if not tool.is_available() and not tool.install():
            return ToolOutcome(f"Error: {name} is not installed and could not be installed.")

        result = _run_single_tool(tool, relevant, self.context.workspace, {})
        if result.errors:
            logger.info("%s reported errors: %s", name, result.errors)
        if not result.findings:
            return ToolOutcome(
                f"{name} found no issues in {len(relevant)} file(s) "
                f"({result.execution_time_ms}ms)."
            )

        self.context.record_analyzer_run(name, result.findings)

        return ToolOutcome(_truncate(_format_tool_findings(name, result.findings)))

    def _tool_git_log(self, args: dict) -> ToolOutcome:
        path = str(args.get("path", ""))
        if not self.context.resolve(path):
            return ToolOutcome(f"Error: '{path}' is outside the repository.")
        count = max(1, min(int(args.get("count") or 5), 20))
        out = self._git(
            ["git", "log", f"-{count}", "--no-color", "--date=short",
             "--pretty=format:%h %ad %an: %s", "--", path]
        )
        if out is None:
            return ToolOutcome("Error: git log failed.")
        return ToolOutcome(out.strip() or f"No commit history for '{path}'.")

    def _tool_read_pr_metadata(self, args: dict) -> ToolOutcome:
        meta = self.context.metadata
        parts = [
            f"**Title:** {meta.title or '(none)'}",
            f"**Author:** {meta.author or '(unknown)'}",
            f"**Base:** {meta.base_ref or '(unknown)'}",
        ]
        if meta.labels:
            parts.append(f"**Labels:** {', '.join(meta.labels)}")
        parts.append(f"\n**Description:**\n{meta.description or '(none provided)'}")
        if meta.comments:
            recent = meta.comments[-10:]
            parts.append("\n**Human comments:**")
            parts.extend(f"- {c[:300]}" for c in recent)
        return ToolOutcome(_truncate("\n".join(parts)))

    def _tool_post_finding(self, args: dict) -> ToolOutcome:
        finding = AgentFinding.from_tool_call(args, source=self.source)
        if finding.path and finding.path not in self.context.diff:
            # Not fatal: a finding may legitimately concern an unchanged caller.
            logger.info("Finding on unchanged file %s", finding.path)
        return ToolOutcome(self.collector.add(finding))

    def _tool_finish(self, args: dict) -> ToolOutcome:
        summary = str(args.get("summary", "")).strip()
        scores = args.get("scores") if isinstance(args.get("scores"), dict) else {}
        return ToolOutcome(
            "Review complete.",
            is_finish=True,
            payload={"summary": summary, "scores": scores},
        )

    # --- helpers ---

    def _git(self, cmd: list[str]) -> str | None:
        """Run a git command in the workspace. None means it could not run."""
        try:
            proc = subprocess.run(
                cmd,
                cwd=self.context.workspace,
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as e:
            logger.warning("git command failed: %s", e)
            return None
        # git grep exits 1 for "no matches", which is a result, not a failure.
        if proc.returncode not in (0, 1):
            logger.info("git %s exited %d: %s", cmd[1], proc.returncode, proc.stderr[:200])
            if not proc.stdout:
                return None
        return proc.stdout


def _escape_regex(name: str) -> str:
    """Escape a symbol name for use inside a larger ERE."""
    return "".join("\\" + c if c in r"\^$.|?*+()[]{}" else c for c in name)


def _format_tool_findings(tool_name: str, findings: list[Finding]) -> str:
    rows = [f"## {tool_name}: {len(findings)} finding(s)"]
    for f in findings[:40]:
        location = f"{f.file}:{f.line}" if f.line else f.file
        rows.append(f"- [{f.severity.upper()}] {location} ({f.rule_id}) {f.message}")
    if len(findings) > 40:
        rows.append(f"... [{len(findings) - 40} more]")
    return "\n".join(rows)
