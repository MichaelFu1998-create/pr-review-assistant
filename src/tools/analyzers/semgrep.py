"""Semgrep — multi-language security and quality scanner."""

import json
import logging
import subprocess

from ..base import BaseTool, Finding, ToolResult
from ..installer import is_command_available, run_install

logger = logging.getLogger(__name__)

SEVERITY_MAP = {
    "ERROR": "high",
    "WARNING": "medium",
    "INFO": "low",
}


class SemgrepTool(BaseTool):
    name = "semgrep"
    languages = [
        "python", "javascript", "typescript", "java", "go", "ruby",
        "rust", "csharp", "cpp", "kotlin", "swift", "php", "scala",
    ]
    category = "security"
    install_cmd = "pip install semgrep"

    def is_available(self) -> bool:
        return is_command_available("semgrep")

    def install(self) -> bool:
        return run_install(self.install_cmd)

    def filter_files(self, files: list[str]) -> list[str]:
        # Semgrep handles all supported languages, no filtering needed
        return files

    def run(self, files: list[str], workspace: str, config: dict) -> ToolResult:
        rulesets = config.get("rulesets", ["p/default"])
        severity = config.get("severity", "INFO")

        cmd = [
            "semgrep",
            "--json",
            "--severity", severity,
            "--no-git-ignore",
            "--quiet",
        ]
        for ruleset in rulesets:
            cmd.extend(["--config", ruleset])
        cmd.extend(files)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=workspace,
                timeout=180,
            )
            return self._parse_output(result.stdout, result.stderr)
        except subprocess.TimeoutExpired:
            return ToolResult(tool_name=self.name, errors=["Semgrep timed out after 180s"])
        except Exception as e:
            return ToolResult(tool_name=self.name, errors=[str(e)])

    def _parse_output(self, stdout: str, stderr: str) -> ToolResult:
        findings = []
        errors = []

        if stderr:
            for line in stderr.strip().split("\n"):
                if line and "error" in line.lower():
                    errors.append(line)

        try:
            data = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError:
            return ToolResult(tool_name=self.name, errors=["Failed to parse Semgrep JSON output"])

        for result in data.get("results", []):
            findings.append(Finding(
                file=result.get("path", ""),
                line=result.get("start", {}).get("line"),
                severity=SEVERITY_MAP.get(result.get("extra", {}).get("severity", ""), "medium"),
                category="security",
                rule_id=result.get("check_id", "unknown"),
                message=result.get("extra", {}).get("message", ""),
                tool=self.name,
                suggestion=result.get("extra", {}).get("fix", None),
            ))

        return ToolResult(tool_name=self.name, findings=findings, errors=errors)
