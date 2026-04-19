"""Ruff — fast Python linter (replaces flake8, pylint, isort, etc.)."""

import json
import logging
import subprocess

from ..base import BaseTool, Finding, ToolResult
from ..installer import is_command_available, run_install

logger = logging.getLogger(__name__)

# Map Ruff rule prefixes to categories and severities
RULE_CATEGORIES = {
    "E": ("quality", "low"),       # pycodestyle errors
    "W": ("quality", "info"),      # pycodestyle warnings
    "F": ("quality", "medium"),    # pyflakes
    "B": ("quality", "medium"),    # bugbear
    "S": ("security", "high"),     # bandit (security)
    "C90": ("quality", "medium"),  # mccabe complexity
    "I": ("quality", "info"),      # isort
    "N": ("quality", "low"),       # pep8-naming
    "UP": ("quality", "low"),      # pyupgrade
    "A": ("quality", "low"),       # builtins
    "T20": ("quality", "info"),    # print statements
    "SIM": ("quality", "low"),     # simplify
    "PTH": ("quality", "low"),     # pathlib
}


class RuffTool(BaseTool):
    name = "ruff"
    languages = ["python"]
    category = "quality"
    install_cmd = "pip install ruff"

    def is_available(self) -> bool:
        return is_command_available("ruff")

    def install(self) -> bool:
        return run_install(self.install_cmd)

    def run(self, files: list[str], workspace: str, config: dict) -> ToolResult:
        select_rules = config.get("select", ["E", "F", "B", "S", "C90", "W"])
        line_length = config.get("line-length", 120)

        cmd = [
            "ruff", "check",
            "--output-format", "json",
            "--line-length", str(line_length),
        ]
        for rule in select_rules:
            cmd.extend(["--select", rule])
        cmd.extend(files)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=workspace,
                timeout=60,
            )
            # Ruff exits with 1 when findings exist, which is normal
            return self._parse_output(result.stdout)
        except subprocess.TimeoutExpired:
            return ToolResult(tool_name=self.name, errors=["Ruff timed out after 60s"])
        except Exception as e:
            return ToolResult(tool_name=self.name, errors=[str(e)])

    def _parse_output(self, stdout: str) -> ToolResult:
        findings = []
        try:
            data = json.loads(stdout) if stdout.strip() else []
        except json.JSONDecodeError:
            return ToolResult(tool_name=self.name, errors=["Failed to parse Ruff JSON output"])

        for item in data:
            code = item.get("code", "")
            category, severity = self._classify_rule(code)
            findings.append(Finding(
                file=item.get("filename", ""),
                line=item.get("location", {}).get("row"),
                severity=severity,
                category=category,
                rule_id=code,
                message=item.get("message", ""),
                tool=self.name,
                suggestion=item.get("fix", {}).get("message") if item.get("fix") else None,
            ))

        return ToolResult(tool_name=self.name, findings=findings)

    def _classify_rule(self, code: str) -> tuple[str, str]:
        """Map a Ruff rule code to (category, severity)."""
        for prefix, (cat, sev) in RULE_CATEGORIES.items():
            if code.startswith(prefix):
                return cat, sev
        return "quality", "low"
