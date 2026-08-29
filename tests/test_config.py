"""Config loading and the action-input / .pr-review.json precedence rules."""

import json

import pytest

from src.config import load_config


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_WORKSPACE", str(tmp_path))
    # Clear every INPUT_ var so a stray one in the environment cannot leak in.
    for key in list(__import__("os").environ):
        if key.startswith("INPUT_"):
            monkeypatch.delenv(key, raising=False)
    return tmp_path


def write_repo_config(workspace, data):
    (workspace / ".pr-review.json").write_text(json.dumps(data))


class TestDefaults:
    def test_defaults_applied_when_nothing_is_set(self, workspace):
        config = load_config()
        assert config.tools == "auto"
        assert config.review_focus == "all"
        assert config.severity_threshold == "low"
        assert config.max_files == 10
        assert config.agent_mode == "agent"

    def test_empty_action_input_falls_back_to_the_default(self, workspace, monkeypatch):
        """The Action always sets every INPUT_ var, empty when unspecified."""
        monkeypatch.setenv("INPUT_TOOLS", "")
        monkeypatch.setenv("INPUT_MAX_FILES", "")
        config = load_config()
        assert config.tools == "auto" and config.max_files == 10
        assert config.review_focus == "all"


class TestRepoConfigPrecedence:
    """Regression: action.yaml used to send a non-empty default for every input,
    so _merge_repo_config's 'was it set?' check was always true and the whole
    `review` section of .pr-review.json was silently ignored."""

    def test_repo_config_applies_when_the_input_is_empty(self, workspace, monkeypatch):
        monkeypatch.setenv("INPUT_TOOLS", "")
        write_repo_config(
            workspace,
            {
                "tools": {"enabled": ["semgrep", "bandit"]},
                "review": {"focus": ["security"], "max_files": 3},
            },
        )
        config = load_config()
        assert config.tools == "semgrep,bandit"
        assert config.review_focus == "security"
        assert config.max_files == 3

    def test_explicit_action_input_beats_repo_config(self, workspace, monkeypatch):
        monkeypatch.setenv("INPUT_REVIEW_FOCUS", "performance")
        write_repo_config(workspace, {"review": {"focus": ["security"]}})
        assert load_config().review_focus == "performance"

    def test_tool_configs_always_apply(self, workspace):
        write_repo_config(
            workspace, {"tools": {"config": {"semgrep": {"rules": "p/owasp-top-ten"}}}}
        )
        assert load_config().tool_configs == {"semgrep": {"rules": "p/owasp-top-ten"}}

    def test_focus_accepts_a_string_or_a_list(self, workspace):
        write_repo_config(workspace, {"review": {"focus": "security"}})
        assert load_config().review_focus == "security"
        write_repo_config(workspace, {"review": {"focus": ["security", "quality"]}})
        assert load_config().review_focus == "security,quality"

    def test_scoring_from_repo_config(self, workspace):
        write_repo_config(workspace, {"review": {"scoring": {"enabled": True}}})
        assert load_config().enable_scoring is True

    def test_malformed_repo_config_is_survivable(self, workspace):
        (workspace / ".pr-review.json").write_text("{not json")
        assert load_config().tools == "auto"


class TestDerivedProperties:
    def test_file_patterns(self, workspace, monkeypatch):
        monkeypatch.setenv("INPUT_FILES", "*.py, src/*.ts")
        assert load_config().file_patterns == ["*.py", "src/*.ts"]

    def test_tools_list_is_empty_for_auto_and_none(self, workspace, monkeypatch):
        monkeypatch.setenv("INPUT_TOOLS", "auto")
        assert load_config().tools_list == []
        monkeypatch.setenv("INPUT_TOOLS", "semgrep, ruff")
        assert load_config().tools_list == ["semgrep", "ruff"]

    def test_focus_areas_expands_all(self, workspace):
        assert set(load_config().focus_areas) == {
            "security", "quality", "performance",
        }

    def test_agent_mode_aliases(self, workspace, monkeypatch):
        """'single' was only a contrast with the removed 'multi' mode."""
        for value, expected in [
            ("", "agent"),
            ("agent", "agent"),
            ("single", "agent"),
            ("multi", "agent"),
            ("pipeline", "pipeline"),
            ("PIPELINE", "pipeline"),
            ("nonsense", "agent"),
        ]:
            monkeypatch.setenv("INPUT_AGENT_MODE", value)
            assert load_config().agent_mode == expected, value


class TestAgentSettings:
    def test_agent_inputs_are_read(self, workspace, monkeypatch):
        monkeypatch.setenv("INPUT_AGENT_MODE", "agent")
        monkeypatch.setenv("INPUT_MAX_AGENT_STEPS", "8")
        monkeypatch.setenv("INPUT_MAX_AGENT_TOKENS", "50000")
        monkeypatch.setenv("INPUT_FAIL_ON", "high")
        monkeypatch.setenv("INPUT_OUTPUT_SARIF", "out.sarif")
        config = load_config()
        assert config.agent_mode == "agent"
        assert config.max_agent_steps == 8
        assert config.max_agent_tokens == 50_000
        assert config.fail_on == "high"
        assert config.output_sarif == "out.sarif"

    def test_numeric_inputs_tolerate_empty_strings(self, workspace, monkeypatch):
        for name in ("MAX_AGENT_STEPS", "MAX_AGENT_TOKENS", "MAX_AGENT_SECONDS",
                     "MAX_FINDINGS", "MAX_TOKENS", "TEMPERATURE", "GITHUB_PR_ID"):
            monkeypatch.setenv(f"INPUT_{name}", "")
        config = load_config()
        assert config.max_agent_steps == 25
        assert config.max_tokens == 32000
        assert config.github_pr_id == 0


class TestProviderNeutralNames:
    """openai_model was fine when OpenAI was the only provider; it actively
    misleads now that the same input names a Grok or Claude model."""

    def test_new_names_are_read(self, workspace, monkeypatch):
        monkeypatch.setenv("INPUT_MODEL", "claude-sonnet-4-6")
        monkeypatch.setenv("INPUT_TEMPERATURE", "0.4")
        monkeypatch.setenv("INPUT_MAX_TOKENS", "9000")
        config = load_config()
        assert config.model == "claude-sonnet-4-6"
        assert config.temperature == 0.4
        assert config.max_tokens == 9000

    def test_defaults(self, workspace):
        config = load_config()
        assert config.model == "grok-4.6"
        assert config.temperature == 1.0
        assert config.max_tokens == 32000

    def test_legacy_names_still_work(self, workspace, monkeypatch):
        """v2.0 workflows must not break."""
        monkeypatch.setenv("INPUT_OPENAI_MODEL", "gpt-5.4-mini-2026-03-17")
        monkeypatch.setenv("INPUT_OPENAI_TEMPERATURE", "0.2")
        monkeypatch.setenv("INPUT_OPENAI_MAX_TOKENS", "4096")
        config = load_config()
        assert config.model == "gpt-5.4-mini-2026-03-17"
        assert config.temperature == 0.2
        assert config.max_tokens == 4096

    def test_legacy_name_warns(self, workspace, monkeypatch, caplog):
        monkeypatch.setenv("INPUT_OPENAI_MODEL", "grok-4.6")
        with caplog.at_level("WARNING"):
            load_config()
        assert "deprecated" in caplog.text
        assert "openai_model" in caplog.text and "'model'" in caplog.text

    def test_new_name_wins_over_legacy(self, workspace, monkeypatch):
        monkeypatch.setenv("INPUT_MODEL", "grok-4.6")
        monkeypatch.setenv("INPUT_OPENAI_MODEL", "gpt-4o")
        assert load_config().model == "grok-4.6"

    def test_new_name_does_not_warn(self, workspace, monkeypatch, caplog):
        monkeypatch.setenv("INPUT_MODEL", "grok-4.6")
        with caplog.at_level("WARNING"):
            load_config()
        assert "deprecated" not in caplog.text

    def test_provider_key_inputs_keep_their_names(self, workspace, monkeypatch):
        """openai_api_key is correctly named: it really is the OpenAI key."""
        monkeypatch.setenv("INPUT_OPENAI_API_KEY", "sk-x")
        monkeypatch.setenv("INPUT_ANTHROPIC_API_KEY", "sk-ant-x")
        monkeypatch.setenv("INPUT_XAI_API_KEY", "xai-x")
        config = load_config()
        assert config.openai_api_key == "sk-x"
        assert config.anthropic_api_key == "sk-ant-x"
        assert config.xai_api_key == "xai-x"


class TestReasoningEffort:
    def test_defaults_to_medium(self, workspace):
        assert load_config().reasoning_effort == "medium"

    def test_explicit_value_wins(self, workspace, monkeypatch):
        monkeypatch.setenv("INPUT_REASONING_EFFORT", "high")
        assert load_config().reasoning_effort == "high"

    def test_empty_input_falls_back_to_medium(self, workspace, monkeypatch):
        monkeypatch.setenv("INPUT_REASONING_EFFORT", "")
        assert load_config().reasoning_effort == "medium"


class TestNoPersonas:
    """review_persona is gone: this is a code review tool, not a teaching mode."""

    def test_config_has_no_persona_field(self, workspace):
        assert not hasattr(load_config(), "review_persona")

    def test_education_is_not_a_focus_area(self, workspace):
        assert "education" not in load_config().focus_areas

    def test_repo_config_persona_is_ignored_not_fatal(self, workspace):
        """An old .pr-review.json must not break the run."""
        write_repo_config(workspace, {"review": {"persona": "mentor", "focus": ["security"]}})
        config = load_config()
        assert config.review_focus == "security"
        assert not hasattr(config, "review_persona")
