"""Hadolint — Dockerfile linter."""

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


class HadolintTool(BaseTool):
    name = "hadolint"
    languages = ["dockerfile"]
    category = "quality"
    install_cmd = None

    def is_available(self) -> bool:
        return is_command_available("hadolint")

    def install(self) -> bool:
        return run_install(
            "curl -sSL https://github.com/hadolint/hadolint/releases/latest/download/hadolint-Linux-x86_64 -o /usr/local/bin/hadolint && chmod +x /usr/local/bin/hadolint"
        )

    def filter_files(self, files: list[str]) -> list[str]:
        return [f for f in files if "Dockerfile" in f.split("/")[-1]]

    def run(self, files: list[str], workspace: str, config: dict) -> ToolResult:
        all_findings = []
        errors = []

        for filepath in files:
            cmd = ["hadolint", "--format", "json", filepath]
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True,
                    cwd=workspace, timeout=30,
                )
                parsed = self._parse_output(result.stdout, filepath)
                all_findings.extend(parsed.findings)
                errors.extend(parsed.errors)
            except Exception as e:
                errors.append(f"Hadolint failed on {filepath}: {e}")

        return ToolResult(tool_name=self.name, findings=all_findings, errors=errors)

    def _parse_output(self, stdout: str, filepath: str) -> ToolResult:
        findings = []
        try:
            data = json.loads(stdout) if stdout.strip() else []
        except json.JSONDecodeError:
            return ToolResult(tool_name=self.name, errors=["Failed to parse Hadolint JSON output"])

        for item in data:
            severity = SEVERITY_MAP.get(item.get("level", ""), "medium")
            findings.append(Finding(
                file=filepath,
                line=item.get("line"),
                severity=severity,
                category="quality",
                rule_id=item.get("code", "unknown"),
                message=item.get("message", ""),
                tool=self.name,
            ))

        return ToolResult(tool_name=self.name, findings=findings)
