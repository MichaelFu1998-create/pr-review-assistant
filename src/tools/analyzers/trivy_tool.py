"""Trivy — comprehensive vulnerability scanner for dependencies and containers."""

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
    "UNKNOWN": "info",
}


class TrivyTool(BaseTool):
    name = "trivy"
    languages = []  # Works on lockfiles, not language-specific
    category = "dependency"
    install_cmd = None

    def is_available(self) -> bool:
        return is_command_available("trivy")

    def install(self) -> bool:
        return run_install(
            "curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin"
        )

    def filter_files(self, files: list[str]) -> list[str]:
        # Trivy scans the whole project, but we only run it if
        # dependency files were changed
        dep_files = [
            "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
            "requirements.txt", "Pipfile.lock", "poetry.lock",
            "pom.xml", "build.gradle", "build.gradle.kts",
            "go.sum", "go.mod",
            "Gemfile.lock", "Cargo.lock",
            "composer.lock",
        ]
        matching = [f for f in files if any(f.endswith(d) for d in dep_files)]
        return matching

    def run(self, files: list[str], workspace: str, config: dict) -> ToolResult:
        cmd = [
            "trivy", "fs",
            "--format", "json",
            "--scanners", "vuln",
            "--quiet",
            workspace,
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                cwd=workspace, timeout=120,
            )
            return self._parse_output(result.stdout)
        except subprocess.TimeoutExpired:
            return ToolResult(tool_name=self.name, errors=["Trivy timed out after 120s"])
        except Exception as e:
            return ToolResult(tool_name=self.name, errors=[str(e)])

    def _parse_output(self, stdout: str) -> ToolResult:
        findings = []
        try:
            data = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError:
            return ToolResult(tool_name=self.name, errors=["Failed to parse Trivy JSON output"])

        for result in data.get("Results", []):
            target = result.get("Target", "")
            for vuln in result.get("Vulnerabilities", []):
                severity = SEVERITY_MAP.get(vuln.get("Severity", ""), "medium")
                pkg = vuln.get("PkgName", "")
                vuln_id = vuln.get("VulnerabilityID", "unknown")
                installed = vuln.get("InstalledVersion", "")
                fixed = vuln.get("FixedVersion", "")

                suggestion = f"Update {pkg} from {installed} to {fixed}" if fixed else None

                findings.append(Finding(
                    file=target,
                    line=None,
                    severity=severity,
                    category="dependency",
                    rule_id=vuln_id,
                    message=f"{pkg}@{installed}: {vuln.get('Title', vuln_id)}",
                    tool=self.name,
                    suggestion=suggestion,
                ))

        return ToolResult(tool_name=self.name, findings=findings)
