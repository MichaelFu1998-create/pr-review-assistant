"""Tool plugin registry — auto-discovers analyzers in the analyzers/ package."""

import importlib
import logging
import pkgutil

from . import analyzers
from .base import BaseTool

logger = logging.getLogger(__name__)

_registry: dict[str, type[BaseTool]] = {}

# Default tools per language (used when tools="auto")
DEFAULT_TOOLS: dict[str, list[str]] = {
    "python": ["semgrep", "ruff", "bandit"],
    "javascript": ["semgrep", "eslint"],
    "typescript": ["semgrep", "eslint"],
    "java": ["semgrep", "pmd", "checkstyle"],
    "go": ["semgrep", "golangci_lint"],
    "ruby": ["semgrep"],
    "rust": ["semgrep"],
    "csharp": ["semgrep"],
    "cpp": ["semgrep"],
    "shell": ["shellcheck"],
    "dockerfile": ["hadolint"],
    "terraform": ["checkov"],
}

# Tools that run regardless of language
UNIVERSAL_TOOLS = ["detect_secrets"]


def discover_tools() -> dict[str, type[BaseTool]]:
    """Auto-discover all BaseTool subclasses in the analyzers package."""
    global _registry
    if _registry:
        return _registry

    for _importer, modname, _ispkg in pkgutil.iter_modules(analyzers.__path__):
        try:
            module = importlib.import_module(f".analyzers.{modname}", package="src.tools")
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, BaseTool)
                    and attr is not BaseTool
                    and hasattr(attr, "name")
                    and attr.name
                ):
                    _registry[attr.name] = attr
                    logger.debug(f"Registered tool: {attr.name}")
        except Exception as e:
            logger.warning(f"Failed to load analyzer module {modname}: {e}")

    logger.info(f"Discovered {len(_registry)} tools: {list(_registry.keys())}")
    return _registry


def get_tools_for_config(
    detected_languages: list[str],
    tools_setting: str,
    explicit_tools: list[str] | None = None,
) -> list[BaseTool]:
    """Get tool instances based on detected stack and configuration.

    Args:
        detected_languages: Languages detected in the PR.
        tools_setting: "auto", "none", or comma-separated tool names.
        explicit_tools: Override list from .pr-review.json.

    Returns:
        List of instantiated tool objects to run.
    """
    registry = discover_tools()

    if tools_setting == "none":
        return []

    if tools_setting != "auto" and not explicit_tools:
        # Explicit tool list from action input
        tool_names = [t.strip() for t in tools_setting.split(",")]
    elif explicit_tools:
        tool_names = explicit_tools
    else:
        # Auto-detect: collect default tools for each detected language
        tool_names = set()
        for lang in detected_languages:
            for tool_name in DEFAULT_TOOLS.get(lang, []):
                tool_names.add(tool_name)
        # Always add universal tools
        tool_names.update(UNIVERSAL_TOOLS)
        tool_names = list(tool_names)

    # Instantiate tools
    instances = []
    for name in tool_names:
        tool_class = registry.get(name)
        if tool_class:
            instances.append(tool_class())
        else:
            logger.warning(f"Unknown tool '{name}', skipping (available: {list(registry.keys())})")

    logger.info(f"Selected tools: {[t.name for t in instances]}")
    return instances
