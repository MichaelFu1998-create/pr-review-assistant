"""Checkstyle — Java code style checker."""

import logging
import subprocess
import xml.etree.ElementTree as ET

from ..base import BaseTool, Finding, ToolResult
from ..installer import is_command_available, run_install

logger = logging.getLogger(__name__)

SEVERITY_MAP = {
    "error": "high",
    "warning": "medium",
    "info": "low",
    "ignore": "info",
}


class CheckstyleTool(BaseTool):
    name = "checkstyle"
    languages = ["java"]
    category = "quality"
    install_cmd = None

    def is_available(self) -> bool:
        return is_command_available("java")

    def install(self) -> bool:
        # Download Checkstyle jar
        version = "10.17.0"
        return run_install(
            f"curl -sSL https://github.com/checkstyle/checkstyle/releases/download/checkstyle-{version}/checkstyle-{version}-all.jar -o /opt/checkstyle.jar"
        )

    def run(self, files: list[str], workspace: str, config: dict) -> ToolResult:
        config_path = config.get("config_path", "")

        cmd = [
            "java", "-jar", "/opt/checkstyle.jar",
            "-c", config_path if config_path else "/google_checks.xml",
            "-f", "xml",
        ]
        cmd.extend(files)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=workspace,
                timeout=120,
            )
            return self._parse_output(result.stdout)
        except subprocess.TimeoutExpired:
            return ToolResult(tool_name=self.name, errors=["Checkstyle timed out after 120s"])
        except Exception as e:
            return ToolResult(tool_name=self.name, errors=[str(e)])

    def _parse_output(self, stdout: str) -> ToolResult:
        findings = []
        if not stdout.strip():
            return ToolResult(tool_name=self.name)

        try:
            root = ET.fromstring(stdout)
        except ET.ParseError:
            return ToolResult(tool_name=self.name, errors=["Failed to parse Checkstyle XML output"])

        for file_elem in root.findall("file"):
            filepath = file_elem.get("name", "")
            for error in file_elem.findall("error"):
                severity = SEVERITY_MAP.get(error.get("severity", ""), "low")
                findings.append(Finding(
                    file=filepath,
                    line=int(error.get("line", 0)) or None,
                    severity=severity,
                    category="quality",
                    rule_id=error.get("source", "unknown").split(".")[-1],
                    message=error.get("message", ""),
                    tool=self.name,
                ))

        return ToolResult(tool_name=self.name, findings=findings)
