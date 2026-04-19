"""ESLint — JavaScript/TypeScript linter with security plugins."""

import json
import logging
import os
import subprocess

from ..base import BaseTool, Finding, ToolResult
from ..installer import is_command_available, run_install

logger = logging.getLogger(__name__)

SEVERITY_MAP = {
    2: "medium",  # error
    1: "low",     # warning
}


class ESLintTool(BaseTool):
    name = "eslint"
    languages = ["javascript", "typescript"]
    category = "quality"
    install_cmd = None  # Complex install, handled in install()

    def is_available(self) -> bool:
        return is_command_available("npx")

    def install(self) -> bool:
        # ESLint is typically a project dependency; we install globally as fallback
        return run_install("npm install -g eslint")

    def run(self, files: list[str], workspace: str, config: dict) -> ToolResult:
        config_path = config.get("config_path", "")

        # Check if project has ESLint config
        has_local_config = any(
            os.path.exists(os.path.join(workspace, f))
            for f in [".eslintrc", ".eslintrc.js", ".eslintrc.json", ".eslintrc.yml", "eslint.config.js", "eslint.config.mjs"]
        )

        cmd = ["npx", "eslint", "--format", "json", "--no-error-on-unmatched-pattern"]

        if config_path:
            cmd.extend(["--config", config_path])
        elif not has_local_config:
            # No project config — skip ESLint since it needs configuration
            logger.info("No ESLint config found in project, skipping")
            return ToolResult(tool_name=self.name)

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
            return ToolResult(tool_name=self.name, errors=["ESLint timed out after 120s"])
        except Exception as e:
            return ToolResult(tool_name=self.name, errors=[str(e)])

    def _parse_output(self, stdout: str) -> ToolResult:
        findings = []
        try:
            data = json.loads(stdout) if stdout.strip() else []
        except json.JSONDecodeError:
            return ToolResult(tool_name=self.name, errors=["Failed to parse ESLint JSON output"])

        for file_result in data:
            filepath = file_result.get("filePath", "")
            for msg in file_result.get("messages", []):
                rule_id = msg.get("ruleId", "unknown") or "parse-error"
                # Detect security-related rules
                category = "security" if any(
                    kw in rule_id for kw in ["security", "no-eval", "no-implied-eval", "no-new-func"]
                ) else "quality"
                severity = "high" if category == "security" else SEVERITY_MAP.get(msg.get("severity", 1), "low")

                findings.append(Finding(
                    file=filepath,
                    line=msg.get("line"),
                    severity=severity,
                    category=category,
                    rule_id=rule_id,
                    message=msg.get("message", ""),
                    tool=self.name,
                    suggestion=msg.get("fix", {}).get("text") if msg.get("fix") else None,
                ))

        return ToolResult(tool_name=self.name, findings=findings)
