"""npm audit — JavaScript/TypeScript dependency vulnerability scanner."""

import json
import logging
import os
import subprocess

from ..base import BaseTool, Finding, ToolResult
from ..installer import is_command_available

logger = logging.getLogger(__name__)

SEVERITY_MAP = {
    "critical": "critical",
    "high": "high",
    "moderate": "medium",
    "low": "low",
    "info": "info",
}


class NpmAuditTool(BaseTool):
    name = "npm_audit"
    languages = ["javascript", "typescript"]
    category = "dependency"
    install_cmd = None  # npm is pre-installed

    def is_available(self) -> bool:
        return is_command_available("npm")

    def install(self) -> bool:
        return True  # npm should be available in the Docker image

    def filter_files(self, files: list[str]) -> list[str]:
        # npm audit works on the whole project, not individual files
        # but we only run it if JS/TS files were changed
        js_ts_files = [f for f in files if f.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs"))]
        return js_ts_files

    def run(self, files: list[str], workspace: str, config: dict) -> ToolResult:
        # Check if package-lock.json or package.json exists
        if not os.path.exists(os.path.join(workspace, "package.json")):
            return ToolResult(tool_name=self.name)

        cmd = ["npm", "audit", "--json"]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=workspace,
                timeout=60,
            )
            return self._parse_output(result.stdout)
        except subprocess.TimeoutExpired:
            return ToolResult(tool_name=self.name, errors=["npm audit timed out after 60s"])
        except Exception as e:
            return ToolResult(tool_name=self.name, errors=[str(e)])

    def _parse_output(self, stdout: str) -> ToolResult:
        findings = []
        try:
            data = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError:
            return ToolResult(tool_name=self.name, errors=["Failed to parse npm audit output"])

        vulnerabilities = data.get("vulnerabilities", {})
        for pkg_name, vuln_info in vulnerabilities.items():
            severity = SEVERITY_MAP.get(vuln_info.get("severity", ""), "medium")
            via = vuln_info.get("via", [])
            # Get the advisory details
            messages = []
            for v in via:
                if isinstance(v, dict):
                    messages.append(v.get("title", ""))
                elif isinstance(v, str):
                    messages.append(f"Vulnerable dependency: {v}")

            findings.append(Finding(
                file="package.json",
                line=None,
                severity=severity,
                category="dependency",
                rule_id=f"npm-vuln-{pkg_name}",
                message=f"{pkg_name}: {'; '.join(messages) if messages else 'Known vulnerability'}",
                tool=self.name,
                suggestion=f"Run 'npm audit fix' or update {pkg_name} to a patched version.",
            ))

        return ToolResult(tool_name=self.name, findings=findings)
