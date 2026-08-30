"""SemgrepTool argv construction.

Regression for a bug that silenced semgrep since v1: --severity is a *filter*
("report findings only from rules matching the supplied severity level"), not a
minimum. Defaulting it to INFO suppressed every WARNING and ERROR rule, so the
flagship security analyser reported nothing in every run.
"""

from src.tools.analyzers.semgrep import SemgrepTool, _severity_filters


def argv(config, monkeypatch):
    seen = {}

    class Result:
        returncode = 0
        stdout = '{"results": [], "errors": []}'
        stderr = ""

    def capture(cmd, *a, **k):
        seen["cmd"] = cmd
        return Result()

    import src.tools.analyzers.semgrep as module
    monkeypatch.setattr(module.subprocess, "run", capture)
    SemgrepTool().run(["a.py"], ".", config)
    return seen["cmd"]


class TestSeverityFilter:
    def test_no_severity_flag_by_default(self, monkeypatch):
        """The bug: --severity INFO meant only INFO rules ever reported."""
        cmd = argv({}, monkeypatch)
        assert "--severity" not in cmd

    def test_explicit_severity_is_honoured(self, monkeypatch):
        cmd = argv({"severity": "ERROR"}, monkeypatch)
        assert cmd[cmd.index("--severity") + 1] == "ERROR"

    def test_several_severities(self, monkeypatch):
        cmd = argv({"severity": ["ERROR", "WARNING"]}, monkeypatch)
        assert cmd.count("--severity") == 2
        assert "ERROR" in cmd and "WARNING" in cmd

    def test_parser(self):
        assert _severity_filters({}) == []
        assert _severity_filters({"severity": None}) == []
        assert _severity_filters({"severity": "error"}) == ["ERROR"]
        assert _severity_filters({"severity": [" warning ", ""]}) == ["WARNING"]


class TestArgv:
    def test_rulesets_become_config_flags(self, monkeypatch):
        cmd = argv({"rulesets": ["p/owasp-top-ten", "/tmp/custom.yaml"]}, monkeypatch)
        assert cmd.count("--config") == 2
        assert "/tmp/custom.yaml" in cmd

    def test_default_ruleset(self, monkeypatch):
        cmd = argv({}, monkeypatch)
        assert cmd[cmd.index("--config") + 1] == "p/default"

    def test_never_allows_arbitrary_code_execution(self, monkeypatch):
        """semgrep's pattern-where-python is gated behind this flag; we must
        never pass it, whatever a repo puts in .pr-review.json."""
        cmd = argv({"rulesets": ["p/default"], "severity": "ERROR"}, monkeypatch)
        assert not any(str(a).startswith("--dangerously") for a in cmd)

    def test_files_are_passed_last(self, monkeypatch):
        cmd = argv({}, monkeypatch)
        assert cmd[-1] == "a.py"
