"""Bandit — Python security linter."""

import json
import logging
import subprocess

from ..base import BaseTool, Finding, ToolResult
from ..installer import is_command_available, run_install

logger = logging.getLogger(__name__)

SEVERITY_MAP = {
    "HIGH": "high",
    "MEDIUM": "medium",
    "LOW": "low",
}

CONFIDENCE_BOOST = {
    "HIGH": 0,      # No change
    "MEDIUM": 0,    # No change
    "LOW": 1,       # Downgrade severity by 1 level
}


class BanditTool(BaseTool):
    name = "bandit"
    languages = ["python"]
    category = "security"
    install_cmd = "pip install bandit"

    def is_available(self) -> bool:
        return is_command_available("bandit")

    def install(self) -> bool:
        return run_install(self.install_cmd)

    def run(self, files: list[str], workspace: str, config: dict) -> ToolResult:
        cmd = [
            "bandit",
            "--format", "json",
            "--quiet",
        ]
        # Add severity filter if configured
        severity = config.get("severity", "")
        if severity:
            cmd.extend(["--severity-level", severity])

        cmd.extend(files)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=workspace,
                timeout=60,
            )
            # Bandit exits with 1 when findings exist
            return self._parse_output(result.stdout)
        except subprocess.TimeoutExpired:
            return ToolResult(tool_name=self.name, errors=["Bandit timed out after 60s"])
        except Exception as e:
            return ToolResult(tool_name=self.name, errors=[str(e)])

    def _parse_output(self, stdout: str) -> ToolResult:
        findings = []
        try:
            data = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError:
            return ToolResult(tool_name=self.name, errors=["Failed to parse Bandit JSON output"])

        for result in data.get("results", []):
            severity = SEVERITY_MAP.get(result.get("issue_severity", ""), "medium")
            findings.append(Finding(
                file=result.get("filename", ""),
                line=result.get("line_number"),
                severity=severity,
                category="security",
                rule_id=result.get("test_id", "unknown"),
                message=f"{result.get('issue_text', '')} (Confidence: {result.get('issue_confidence', 'MEDIUM')})",
                tool=self.name,
                suggestion=result.get("more_info"),
            ))

        return ToolResult(tool_name=self.name, findings=findings)
