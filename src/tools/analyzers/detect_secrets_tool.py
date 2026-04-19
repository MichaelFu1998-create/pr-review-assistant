"""detect-secrets — cross-language secret detection."""

import json
import logging
import subprocess

from ..base import BaseTool, Finding, ToolResult
from ..installer import is_command_available, run_install

logger = logging.getLogger(__name__)


class DetectSecretsTool(BaseTool):
    name = "detect_secrets"
    languages = []  # Language-agnostic
    category = "secret"
    install_cmd = "pip install detect-secrets"

    def is_available(self) -> bool:
        return is_command_available("detect-secrets")

    def install(self) -> bool:
        return run_install(self.install_cmd)

    def filter_files(self, files: list[str]) -> list[str]:
        # Scan all files for secrets
        return files

    def run(self, files: list[str], workspace: str, config: dict) -> ToolResult:
        cmd = [
            "detect-secrets", "scan",
            "--list-all-plugins",
        ]
        # Scan specific files
        scan_cmd = ["detect-secrets", "scan"] + files

        try:
            result = subprocess.run(
                scan_cmd,
                capture_output=True,
                text=True,
                cwd=workspace,
                timeout=60,
            )
            return self._parse_output(result.stdout)
        except subprocess.TimeoutExpired:
            return ToolResult(tool_name=self.name, errors=["detect-secrets timed out after 60s"])
        except Exception as e:
            return ToolResult(tool_name=self.name, errors=[str(e)])

    def _parse_output(self, stdout: str) -> ToolResult:
        findings = []
        try:
            data = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError:
            return ToolResult(tool_name=self.name, errors=["Failed to parse detect-secrets output"])

        for filepath, secrets in data.get("results", {}).items():
            for secret in secrets:
                findings.append(Finding(
                    file=filepath,
                    line=secret.get("line_number"),
                    severity="critical",
                    category="secret",
                    rule_id=secret.get("type", "unknown"),
                    message=f"Potential {secret.get('type', 'secret')} detected",
                    tool=self.name,
                    suggestion="Remove this secret and use environment variables or a secrets manager instead.",
                ))

        return ToolResult(tool_name=self.name, findings=findings)
