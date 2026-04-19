"""PMD — Java static analysis for bugs and code quality."""

import json
import logging
import os
import subprocess

from ..base import BaseTool, Finding, ToolResult
from ..installer import is_command_available, run_install

logger = logging.getLogger(__name__)

PRIORITY_MAP = {
    1: "critical",
    2: "high",
    3: "medium",
    4: "low",
    5: "info",
}


class PMDTool(BaseTool):
    name = "pmd"
    languages = ["java"]
    category = "quality"
    install_cmd = None  # Requires download

    def is_available(self) -> bool:
        return is_command_available("pmd")

    def install(self) -> bool:
        # Download PMD binary
        version = "7.0.0"
        cmds = [
            f"curl -sSL https://github.com/pmd/pmd/releases/download/pmd_releases%2F{version}/pmd-dist-{version}-bin.zip -o /tmp/pmd.zip",
            "unzip -q /tmp/pmd.zip -d /opt/",
            f"ln -sf /opt/pmd-bin-{version}/bin/pmd /usr/local/bin/pmd",
        ]
        for cmd in cmds:
            if not run_install(cmd):
                return False
        return True

    def run(self, files: list[str], workspace: str, config: dict) -> ToolResult:
        rulesets = config.get("rulesets", "rulesets/java/quickstart.xml")
        file_list = ",".join(files)

        cmd = [
            "pmd", "check",
            "--format", "json",
            "--rulesets", rulesets,
            "--file-list", "-",
        ]

        try:
            result = subprocess.run(
                cmd,
                input="\n".join(files),
                capture_output=True,
                text=True,
                cwd=workspace,
                timeout=120,
            )
            return self._parse_output(result.stdout)
        except subprocess.TimeoutExpired:
            return ToolResult(tool_name=self.name, errors=["PMD timed out after 120s"])
        except Exception as e:
            return ToolResult(tool_name=self.name, errors=[str(e)])

    def _parse_output(self, stdout: str) -> ToolResult:
        findings = []
        try:
            data = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError:
            return ToolResult(tool_name=self.name, errors=["Failed to parse PMD JSON output"])

        for file_data in data.get("files", []):
            filepath = file_data.get("filename", "")
            for violation in file_data.get("violations", []):
                priority = violation.get("priority", 3)
                findings.append(Finding(
                    file=filepath,
                    line=violation.get("beginline"),
                    severity=PRIORITY_MAP.get(priority, "medium"),
                    category="quality",
                    rule_id=violation.get("rule", "unknown"),
                    message=violation.get("description", ""),
                    tool=self.name,
                    suggestion=violation.get("externalInfoUrl"),
                ))

        return ToolResult(tool_name=self.name, findings=findings)
