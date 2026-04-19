"""golangci-lint — Go linter aggregator."""

import json
import logging
import subprocess

from ..base import BaseTool, Finding, ToolResult
from ..installer import is_command_available, run_install

logger = logging.getLogger(__name__)


class GolangCILintTool(BaseTool):
    name = "golangci_lint"
    languages = ["go"]
    category = "quality"
    install_cmd = None

    def is_available(self) -> bool:
        return is_command_available("golangci-lint")

    def install(self) -> bool:
        return run_install(
            "curl -sSfL https://raw.githubusercontent.com/golangci/golangci-lint/master/install.sh | sh -s -- -b /usr/local/bin"
        )

    def run(self, files: list[str], workspace: str, config: dict) -> ToolResult:
        cmd = [
            "golangci-lint", "run",
            "--out-format", "json",
            "--timeout", "120s",
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=workspace,
                timeout=180,
            )
            return self._parse_output(result.stdout, files)
        except subprocess.TimeoutExpired:
            return ToolResult(tool_name=self.name, errors=["golangci-lint timed out after 180s"])
        except Exception as e:
            return ToolResult(tool_name=self.name, errors=[str(e)])

    def _parse_output(self, stdout: str, changed_files: list[str]) -> ToolResult:
        findings = []
        try:
            data = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError:
            return ToolResult(tool_name=self.name, errors=["Failed to parse golangci-lint output"])

        for issue in data.get("Issues", []):
            filepath = issue.get("Pos", {}).get("Filename", "")
            # Only report findings for changed files
            if filepath not in changed_files:
                continue

            linter = issue.get("FromLinter", "unknown")
            category = "security" if linter in ("gosec", "gocritic") else "quality"
            severity = "high" if category == "security" else "medium"

            findings.append(Finding(
                file=filepath,
                line=issue.get("Pos", {}).get("Line"),
                severity=severity,
                category=category,
                rule_id=f"{linter}:{issue.get('Text', '')[:30]}",
                message=issue.get("Text", ""),
                tool=self.name,
                suggestion=issue.get("Replacement", {}).get("NeedChange"),
            ))

        return ToolResult(tool_name=self.name, findings=findings)
