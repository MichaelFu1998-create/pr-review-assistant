"""Parallel tool execution engine."""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .base import BaseTool, Finding, ToolResult, SEVERITY_ORDER

logger = logging.getLogger(__name__)


def run_tools(
    tools: list[BaseTool],
    files: list[str],
    workspace: str,
    tool_configs: dict,
    severity_threshold: str = "low",
    max_workers: int = 4,
) -> list[Finding]:
    """Run multiple tools in parallel and return deduplicated findings.

    Tool failures are non-fatal — they log a warning and continue.
    """
    all_findings: list[Finding] = []
    threshold_order = SEVERITY_ORDER.get(severity_threshold, 3)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for tool in tools:
            relevant_files = tool.filter_files(files)
            if not relevant_files:
                logger.info(f"Skipping {tool.name}: no relevant files")
                continue

            # Ensure tool is available
            if not tool.is_available():
                logger.info(f"Installing {tool.name}...")
                if not tool.install():
                    logger.warning(f"Failed to install {tool.name}, skipping")
                    continue

            config = tool_configs.get(tool.name, {})
            future = executor.submit(_run_single_tool, tool, relevant_files, workspace, config)
            futures[future] = tool

        for future in as_completed(futures):
            tool = futures[future]
            try:
                result: ToolResult = future.result(timeout=300)
                if result.errors:
                    for error in result.errors:
                        logger.warning(f"{tool.name} error: {error}")

                # Filter by severity threshold
                filtered = [
                    f for f in result.findings
                    if SEVERITY_ORDER.get(f.severity, 4) <= threshold_order
                ]
                all_findings.extend(filtered)
                logger.info(
                    f"{tool.name}: {len(filtered)} findings "
                    f"({len(result.findings) - len(filtered)} below threshold) "
                    f"in {result.execution_time_ms}ms"
                )
            except Exception as e:
                logger.warning(f"Tool {tool.name} failed: {e}")

    return _deduplicate(all_findings)


def _run_single_tool(
    tool: BaseTool, files: list[str], workspace: str, config: dict
) -> ToolResult:
    """Run a single tool with timing."""
    start = time.monotonic()
    try:
        result = tool.run(files, workspace, config)
    except Exception as e:
        result = ToolResult(
            tool_name=tool.name,
            errors=[str(e)],
        )
    result.execution_time_ms = int((time.monotonic() - start) * 1000)
    return result


def _deduplicate(findings: list[Finding]) -> list[Finding]:
    """Remove duplicate findings (same file, line, and rule from different tools)."""
    seen: set[tuple] = set()
    unique: list[Finding] = []

    for f in findings:
        key = (f.file, f.line, f.message[:80])
        if key not in seen:
            seen.add(key)
            unique.append(f)

    if len(findings) != len(unique):
        logger.info(f"Deduplicated {len(findings)} → {len(unique)} findings")

    return unique
