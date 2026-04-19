"""ShellCheck — shell script analysis tool."""

import json
import logging
import subprocess

from ..base import BaseTool, Finding, ToolResult
from ..installer import is_command_available, run_install

logger = logging.getLogger(__name__)

SEVERITY_MAP = {
    "error": "high",
    "warning": "medium",
    "info": "low",
    "style": "info",
}


class ShellCheckTool(BaseTool):
    name = "shellcheck"
    languages = ["shell"]
    category = "quality"
    install_cmd = "apt-get install -y shellcheck"

    def is_available(self) -> bool:
        return is_command_available("shellcheck")

    def install(self) -> bool:
        return run_install(self.install_cmd)

    def run(self, files: list[str], workspace: str, config: dict) -> ToolResult:
        cmd = ["shellcheck", "--format", "json"] + files

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                cwd=workspace, timeout=60,
            )
            return self._parse_output(result.stdout)
        except subprocess.TimeoutExpired:
            return ToolResult(tool_name=self.name, errors=["ShellCheck timed out after 60s"])
        except Exception as e:
            return ToolResult(tool_name=self.name, errors=[str(e)])

    def _parse_output(self, stdout: str) -> ToolResult:
        findings = []
        try:
            data = json.loads(stdout) if stdout.strip() else []
        except json.JSONDecodeError:
            return ToolResult(tool_name=self.name, errors=["Failed to parse ShellCheck output"])

        for item in data:
            severity = SEVERITY_MAP.get(item.get("level", ""), "medium")
            findings.append(Finding(
                file=item.get("file", ""),
                line=item.get("line"),
                severity=severity,
                category="quality",
                rule_id=f"SC{item.get('code', 'unknown')}",
                message=item.get("message", ""),
                tool=self.name,
                suggestion=item.get("fix", {}).get("replacements", [{}])[0].get("replacement") if item.get("fix") else None,
            ))

        return ToolResult(tool_name=self.name, findings=findings)
