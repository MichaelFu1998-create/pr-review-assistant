"""Configuration loading from environment variables and .pr-review.json."""

import json
import os
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Config:
    # Required
    openai_api_key: str = ""
    github_token: str = ""
    github_pr_id: int = 0

    # LLM settings
    llm_provider: str = "openai"
    openai_model: str = "gpt-5.4-mini-2026-03-17"
    openai_temperature: float = 1.0
    openai_max_tokens: int = 32000
    api_base_url: str = ""
    anthropic_api_key: str = ""

    # File filtering
    files: str = "*"
    max_files: int = 10

    # Tool settings
    tools: str = "auto"
    severity_threshold: str = "low"

    # Review settings
    review_focus: str = "all"
    review_persona: str = "normal"
    custom_instructions: str = ""
    enable_scoring: bool = False

    # System
    logging_level: str = "warning"

    # Tool-specific configs from .pr-review.json
    tool_configs: dict = field(default_factory=dict)

    @property
    def file_patterns(self) -> list[str]:
        return [p.strip() for p in self.files.split(",")]

    @property
    def tools_list(self) -> list[str]:
        if self.tools in ("auto", "none"):
            return []
        return [t.strip() for t in self.tools.split(",")]

    @property
    def focus_areas(self) -> list[str]:
        if self.review_focus == "all":
            return ["security", "quality", "performance", "education"]
        return [f.strip() for f in self.review_focus.split(",")]


def _env(name: str, default: str = "") -> str:
    """Read an INPUT_ env var (GitHub Actions convention for Docker actions)."""
    return os.environ.get(f"INPUT_{name.upper()}", default)


def load_config() -> Config:
    """Load configuration from environment variables, overlaid with .pr-review.json if present."""
    config = Config(
        openai_api_key=_env("OPENAI_API_KEY"),
        github_token=_env("GITHUB_TOKEN"),
        github_pr_id=int(_env("GITHUB_PR_ID", "0")),
        llm_provider=_env("LLM_PROVIDER", "openai"),
        openai_model=_env("OPENAI_MODEL", "gpt-5.4-mini-2026-03-17"),
        openai_temperature=float(_env("OPENAI_TEMPERATURE", "1")),
        openai_max_tokens=int(_env("OPENAI_MAX_TOKENS", "32000")),
        api_base_url=_env("API_BASE_URL", ""),
        anthropic_api_key=_env("ANTHROPIC_API_KEY", ""),
        files=_env("FILES", "*"),
        max_files=int(_env("MAX_FILES", "10")),
        tools=_env("TOOLS", "auto"),
        severity_threshold=_env("SEVERITY_THRESHOLD", "low"),
        review_focus=_env("REVIEW_FOCUS", "all"),
        review_persona=_env("REVIEW_PERSONA", "normal"),
        custom_instructions=_env("CUSTOM_INSTRUCTIONS", ""),
        enable_scoring=_env("ENABLE_SCORING", "false").lower() == "true",
        logging_level=_env("LOGGING", "warning"),
    )

    # Overlay .pr-review.json if it exists
    workspace = os.environ.get("GITHUB_WORKSPACE", ".")
    repo_config_path = os.path.join(workspace, ".pr-review.json")
    if os.path.exists(repo_config_path):
        try:
            with open(repo_config_path) as f:
                repo_config = json.load(f)
            _merge_repo_config(config, repo_config)
            logger.info("Loaded repo config from .pr-review.json")
        except Exception as e:
            logger.warning(f"Failed to load .pr-review.json: {e}")

    return config


def _merge_repo_config(config: Config, repo_config: dict) -> None:
    """Merge .pr-review.json into config. Env vars take priority over file config."""
    tools_section = repo_config.get("tools", {})
    review_section = repo_config.get("review", {})

    # Only apply file config if env var wasn't explicitly set
    if _env("TOOLS") == "" and "enabled" in tools_section:
        config.tools = ",".join(tools_section["enabled"])

    if "config" in tools_section:
        config.tool_configs = tools_section["config"]

    if _env("REVIEW_FOCUS") == "" and "focus" in review_section:
        focus = review_section["focus"]
        config.review_focus = ",".join(focus) if isinstance(focus, list) else focus

    if _env("REVIEW_PERSONA") == "" and "persona" in review_section:
        config.review_persona = review_section["persona"]

    if _env("CUSTOM_INSTRUCTIONS") == "" and "custom_instructions" in review_section:
        config.custom_instructions = review_section["custom_instructions"]

    if _env("SEVERITY_THRESHOLD") == "" and "severity_threshold" in review_section:
        config.severity_threshold = review_section["severity_threshold"]

    if _env("MAX_FILES") == "" and "max_files" in review_section:
        config.max_files = review_section["max_files"]

    scoring = review_section.get("scoring", {})
    if _env("ENABLE_SCORING") == "" and scoring.get("enabled"):
        config.enable_scoring = True
