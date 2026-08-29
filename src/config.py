"""Configuration loading from environment variables and .pr-review.json."""

import json
import os
import logging
from dataclasses import dataclass, field

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
    xai_api_key: str = ""

    # File filtering
    files: str = "*"
    max_files: int = 10

    # Tool settings
    tools: str = "auto"
    severity_threshold: str = "low"

    # Agent settings (v2)
    agent_mode: str = "pipeline"        # pipeline (v1) | single | multi
    max_agent_steps: int = 25
    max_agent_tokens: int = 150_000
    max_agent_seconds: float = 600.0
    max_findings: int = 100
    specialists: str = ""               # multi mode; empty means all

    # Review settings
    review_focus: str = "all"
    review_persona: str = "normal"
    custom_instructions: str = ""
    enable_scoring: bool = False

    # Output surfaces (v2)
    output_sarif: str = ""              # path to write SARIF to; empty disables
    output_json: str = ""               # path to write the JSON report to
    fail_on: str = ""                   # severity threshold that fails the run

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
    def specialists_list(self) -> list[str]:
        return [s.strip() for s in self.specialists.split(",") if s.strip()]

    @property
    def focus_areas(self) -> list[str]:
        if self.review_focus == "all":
            return ["security", "quality", "performance", "education"]
        return [f.strip() for f in self.review_focus.split(",")]


def _env(name: str, default: str = "") -> str:
    """Read an INPUT_ env var (GitHub Actions convention for Docker actions)."""
    return os.environ.get(f"INPUT_{name.upper()}", default)


# Settings a repository may override through .pr-review.json. Their action
# inputs default to "" so that "unset" is distinguishable from "explicitly set
# to the default value"; the real defaults are applied after the merge.
#
# Before this, action.yaml sent a non-empty default for every input, so
# _merge_repo_config's "was the env var set?" check was always true and every
# `review` setting in .pr-review.json was silently ignored.
OVERRIDABLE_DEFAULTS: dict[str, object] = {
    "tools": "auto",
    "severity_threshold": "low",
    "review_focus": "all",
    "review_persona": "normal",
    "max_files": 10,
}


def load_config() -> Config:
    """Load configuration from environment variables, overlaid with .pr-review.json if present."""
    config = Config(
        openai_api_key=_env("OPENAI_API_KEY"),
        github_token=_env("GITHUB_TOKEN"),
        github_pr_id=int(_env("GITHUB_PR_ID", "0") or "0"),
        llm_provider=_env("LLM_PROVIDER", "") or "openai",
        openai_model=_env("OPENAI_MODEL", "") or "gpt-5.4-mini-2026-03-17",
        openai_temperature=float(_env("OPENAI_TEMPERATURE", "") or "1"),
        openai_max_tokens=int(_env("OPENAI_MAX_TOKENS", "") or "32000"),
        api_base_url=_env("API_BASE_URL", ""),
        anthropic_api_key=_env("ANTHROPIC_API_KEY", ""),
        xai_api_key=_env("XAI_API_KEY", ""),
        files=_env("FILES", "") or "*",
        agent_mode=_env("AGENT_MODE", "") or "pipeline",
        max_agent_steps=int(_env("MAX_AGENT_STEPS", "") or "25"),
        max_agent_tokens=int(_env("MAX_AGENT_TOKENS", "") or "150000"),
        max_agent_seconds=float(_env("MAX_AGENT_SECONDS", "") or "600"),
        max_findings=int(_env("MAX_FINDINGS", "") or "100"),
        specialists=_env("SPECIALISTS", ""),
        custom_instructions=_env("CUSTOM_INSTRUCTIONS", ""),
        output_sarif=_env("OUTPUT_SARIF", ""),
        output_json=_env("OUTPUT_JSON", ""),
        fail_on=_env("FAIL_ON", ""),
        logging_level=_env("LOGGING", "") or "warning",
        # Left empty on purpose so .pr-review.json can win; defaulted below.
        tools=_env("TOOLS", ""),
        severity_threshold=_env("SEVERITY_THRESHOLD", ""),
        review_focus=_env("REVIEW_FOCUS", ""),
        review_persona=_env("REVIEW_PERSONA", ""),
        max_files=int(_env("MAX_FILES", "") or "0"),
        enable_scoring=_env("ENABLE_SCORING", "").lower() == "true",
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

    _apply_defaults(config)
    return config


def _apply_defaults(config: Config) -> None:
    """Fill in defaults for any overridable setting still unset.

    Runs after the repo config merge, so an explicit action input wins over
    .pr-review.json, which wins over these.
    """
    for name, default in OVERRIDABLE_DEFAULTS.items():
        current = getattr(config, name)
        if current in ("", 0, None):
            setattr(config, name, default)


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
