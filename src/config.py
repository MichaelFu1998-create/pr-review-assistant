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
    llm_provider: str = "xai"
    model: str = "grok-4.6"
    temperature: float = 1.0
    max_tokens: int = 32000
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
    agent_mode: str = "agent"           # agent (default) | adaptive | pipeline (v1)
    max_agent_steps: int = 25
    max_agent_tokens: int = 150_000
    max_agent_seconds: float = 600.0
    max_findings: int = 100
    max_custom_rules: int = 10           # adaptive mode: cap on authored rules
    reasoning_effort: str = "medium"     # reasoning models: low|medium|high|xhigh
    suggest_fixes: bool = True           # render applyable GitHub suggestions

    # Review settings
    review_focus: str = "all"
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
    def focus_areas(self) -> list[str]:
        if self.review_focus == "all":
            return ["security", "quality", "performance"]
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
    "max_files": 10,
}


def _renamed(current: str, legacy: str) -> str:
    """Read an input that used to have an `openai_`-prefixed name.

    `openai_model` was a reasonable name when OpenAI was the only provider; it
    is actively misleading now that it also names Grok and Claude models. The
    old inputs keep working so v2.0 workflows do not break, but using one warns.
    """
    value = _env(current, "")
    if value:
        return value

    legacy_value = _env(legacy, "")
    if legacy_value:
        logger.warning(
            "Input '%s' is deprecated and will be removed in v3; rename it to "
            "'%s'. The openai_ prefix was misleading — this setting applies to "
            "whichever provider is selected.",
            legacy.lower(),
            current.lower(),
        )
    return legacy_value


def _normalize_agent_mode(value: str) -> str:
    """Resolve the agent_mode input, tolerating the retired names.

    'single' was only ever a contrast with 'multi'; with multi gone the mode is
    just 'agent'. Both retired names still resolve so workflows written against
    v2.0 keep running, but 'multi' warns, since it silently gets less than it
    asked for.
    """
    mode = (value or "").strip().lower()
    if mode in ("", "agent", "single"):
        return "agent"
    if mode in ("adaptive", "evolving"):
        return "adaptive"
    if mode == "pipeline":
        return "pipeline"
    if mode == "multi":
        logger.warning(
            "agent_mode 'multi' has been removed; running in 'agent' mode instead."
        )
        return "agent"
    logger.warning("Unknown agent_mode '%s'; using 'agent'.", value)
    return "agent"


def load_config() -> Config:
    """Load configuration from environment variables, overlaid with .pr-review.json if present."""
    config = Config(
        openai_api_key=_env("OPENAI_API_KEY"),
        github_token=_env("GITHUB_TOKEN"),
        github_pr_id=int(_env("GITHUB_PR_ID", "0") or "0"),
        llm_provider=_env("LLM_PROVIDER", "") or "xai",
        model=_renamed("MODEL", "OPENAI_MODEL") or "grok-4.6",
        temperature=float(_renamed("TEMPERATURE", "OPENAI_TEMPERATURE") or "1"),
        max_tokens=int(_renamed("MAX_TOKENS", "OPENAI_MAX_TOKENS") or "32000"),
        api_base_url=_env("API_BASE_URL", ""),
        anthropic_api_key=_env("ANTHROPIC_API_KEY", ""),
        xai_api_key=_env("XAI_API_KEY", ""),
        files=_env("FILES", "") or "*",
        agent_mode=_normalize_agent_mode(_env("AGENT_MODE", "")),
        max_agent_steps=int(_env("MAX_AGENT_STEPS", "") or "25"),
        max_agent_tokens=int(_env("MAX_AGENT_TOKENS", "") or "0"),
        max_agent_seconds=float(_env("MAX_AGENT_SECONDS", "") or "600"),
        max_findings=int(_env("MAX_FINDINGS", "") or "100"),
        max_custom_rules=int(_env("MAX_CUSTOM_RULES", "") or "10"),
        reasoning_effort=_env("REASONING_EFFORT", "") or "medium",
        suggest_fixes=(_env("SUGGEST_FIXES", "") or "true").lower() == "true",
        custom_instructions=_env("CUSTOM_INSTRUCTIONS", ""),
        output_sarif=_env("OUTPUT_SARIF", ""),
        output_json=_env("OUTPUT_JSON", ""),
        fail_on=_env("FAIL_ON", ""),
        logging_level=_env("LOGGING", "") or "warning",
        # Left empty on purpose so .pr-review.json can win; defaulted below.
        tools=_env("TOOLS", ""),
        severity_threshold=_env("SEVERITY_THRESHOLD", ""),
        review_focus=_env("REVIEW_FOCUS", ""),
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
    if config.max_agent_tokens == 0:
        # Recon + authoring + review is roughly 1.5-2x a plain review.
        config.max_agent_tokens = 250_000 if config.agent_mode == "adaptive" else 150_000
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

    if _env("CUSTOM_INSTRUCTIONS") == "" and "custom_instructions" in review_section:
        config.custom_instructions = review_section["custom_instructions"]

    if _env("SEVERITY_THRESHOLD") == "" and "severity_threshold" in review_section:
        config.severity_threshold = review_section["severity_threshold"]

    if _env("MAX_FILES") == "" and "max_files" in review_section:
        config.max_files = review_section["max_files"]

    scoring = review_section.get("scoring", {})
    if _env("ENABLE_SCORING") == "" and scoring.get("enabled"):
        config.enable_scoring = True
