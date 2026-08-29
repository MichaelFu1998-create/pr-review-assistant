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
        assert config.review_persona == "normal"
        assert config.severity_threshold == "low"
        assert config.max_files == 10
        assert config.agent_mode == "pipeline"

    def test_empty_action_input_falls_back_to_the_default(self, workspace, monkeypatch):
        """The Action always sets every INPUT_ var, empty when unspecified."""
        monkeypatch.setenv("INPUT_TOOLS", "")
        monkeypatch.setenv("INPUT_MAX_FILES", "")
        monkeypatch.setenv("INPUT_REVIEW_PERSONA", "")
        config = load_config()
        assert config.tools == "auto" and config.max_files == 10
        assert config.review_persona == "normal"


class TestRepoConfigPrecedence:
    """Regression: action.yaml used to send a non-empty default for every input,
    so _merge_repo_config's 'was it set?' check was always true and the whole
    `review` section of .pr-review.json was silently ignored."""

    def test_repo_config_applies_when_the_input_is_empty(self, workspace, monkeypatch):
        monkeypatch.setenv("INPUT_TOOLS", "")
        monkeypatch.setenv("INPUT_REVIEW_PERSONA", "")
        write_repo_config(
            workspace,
            {
                "tools": {"enabled": ["semgrep", "bandit"]},
                "review": {"persona": "mentor", "focus": ["security"], "max_files": 3},
            },
        )
        config = load_config()
        assert config.tools == "semgrep,bandit"
        assert config.review_persona == "mentor"
        assert config.review_focus == "security"
        assert config.max_files == 3

    def test_explicit_action_input_beats_repo_config(self, workspace, monkeypatch):
        monkeypatch.setenv("INPUT_REVIEW_PERSONA", "security-auditor")
        write_repo_config(workspace, {"review": {"persona": "mentor"}})
        assert load_config().review_persona == "security-auditor"

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
            "security", "quality", "performance", "education",
        }

    def test_specialists_list(self, workspace, monkeypatch):
        monkeypatch.setenv("INPUT_SPECIALISTS", "security, testing")
        assert load_config().specialists_list == ["security", "testing"]
        monkeypatch.setenv("INPUT_SPECIALISTS", "")
        assert load_config().specialists_list == []


class TestAgentSettings:
    def test_agent_inputs_are_read(self, workspace, monkeypatch):
        monkeypatch.setenv("INPUT_AGENT_MODE", "multi")
        monkeypatch.setenv("INPUT_MAX_AGENT_STEPS", "8")
        monkeypatch.setenv("INPUT_MAX_AGENT_TOKENS", "50000")
        monkeypatch.setenv("INPUT_FAIL_ON", "high")
        monkeypatch.setenv("INPUT_OUTPUT_SARIF", "out.sarif")
        config = load_config()
        assert config.agent_mode == "multi"
        assert config.max_agent_steps == 8
        assert config.max_agent_tokens == 50_000
        assert config.fail_on == "high"
        assert config.output_sarif == "out.sarif"

    def test_numeric_inputs_tolerate_empty_strings(self, workspace, monkeypatch):
        for name in ("MAX_AGENT_STEPS", "MAX_AGENT_TOKENS", "MAX_AGENT_SECONDS",
                     "MAX_FINDINGS", "OPENAI_MAX_TOKENS", "OPENAI_TEMPERATURE",
                     "GITHUB_PR_ID"):
            monkeypatch.setenv(f"INPUT_{name}", "")
        config = load_config()
        assert config.max_agent_steps == 25
        assert config.openai_max_tokens == 32000
        assert config.github_pr_id == 0
