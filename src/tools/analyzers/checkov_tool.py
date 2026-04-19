"""Checkov — Infrastructure as Code scanner (Terraform, CloudFormation, K8s, Docker)."""

import json
import logging
import subprocess

from ..base import BaseTool, Finding, ToolResult
from ..installer import is_command_available, run_install

logger = logging.getLogger(__name__)

SEVERITY_MAP = {
    "CRITICAL": "critical",
    "HIGH": "high",
    "MEDIUM": "medium",
    "LOW": "low",
}


class CheckovTool(BaseTool):
    name = "checkov"
    languages = ["terraform", "dockerfile"]
    category = "security"
    install_cmd = "pip install checkov"

    def is_available(self) -> bool:
        return is_command_available("checkov")

    def install(self) -> bool:
        return run_install(self.install_cmd)

    def filter_files(self, files: list[str]) -> list[str]:
        iac_exts = [".tf", ".yaml", ".yml", ".json"]
        return [
            f for f in files
            if any(f.endswith(ext) for ext in iac_exts)
            or "Dockerfile" in f.split("/")[-1]
        ]

    def run(self, files: list[str], workspace: str, config: dict) -> ToolResult:
        cmd = [
            "checkov",
            "--directory", workspace,
            "--output", "json",
            "--quiet",
            "--compact",
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                cwd=workspace, timeout=180,
            )
            return self._parse_output(result.stdout, files)
        except subprocess.TimeoutExpired:
            return ToolResult(tool_name=self.name, errors=["Checkov timed out after 180s"])
        except Exception as e:
            return ToolResult(tool_name=self.name, errors=[str(e)])

    def _parse_output(self, stdout: str, changed_files: list[str]) -> ToolResult:
        findings = []
        try:
            data = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError:
            return ToolResult(tool_name=self.name, errors=["Failed to parse Checkov output"])

        # Checkov may return a list or dict depending on framework count
        results_list = data if isinstance(data, list) else [data]

        for check_result in results_list:
            for failed in check_result.get("results", {}).get("failed_checks", []):
                filepath = failed.get("file_path", "").lstrip("/")
                # Only report findings for changed files
                if not any(filepath.endswith(cf) or cf.endswith(filepath) for cf in changed_files):
                    continue

                severity = SEVERITY_MAP.get(failed.get("severity", ""), "medium")
                findings.append(Finding(
                    file=filepath,
                    line=failed.get("file_line_range", [None])[0],
                    severity=severity,
                    category="security",
                    rule_id=failed.get("check_id", "unknown"),
                    message=failed.get("check_result", {}).get("evaluated_keys", [""])[0]
                            or failed.get("name", ""),
                    tool=self.name,
                    suggestion=failed.get("guideline"),
                ))

        return ToolResult(tool_name=self.name, findings=findings)
