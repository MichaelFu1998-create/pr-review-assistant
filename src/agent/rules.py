"""Agent-authored semgrep rules.

`adaptive` mode lets the reviewer build detectors for the repository in front of
it — a rule for *this* project's auth decorator, *this* project's money type.

The rules are **semgrep YAML, not code**. That is the whole point. Semgrep
matches patterns with its own engine, so an authored rule cannot read a file,
open a socket, or touch the environment. Our runtime holds a GitHub token with
write access and the LLM API keys, and the repository under review is untrusted
input; letting a model write executable code here would turn a prompt injection
into credential theft.

This module is the boundary that keeps "declarative" true. Everything the agent
writes passes through `validate_rule` before semgrep ever sees it.
"""

import logging
import os
import re
import subprocess
import tempfile

from dataclasses import dataclass, field

import yaml

logger = logging.getLogger(__name__)

# Semgrep's one arbitrary-code-execution feature. It is gated behind
# --dangerously-allow-arbitrary-code-execution-from-rules, which we never pass —
# but a rule carrying it is a clear signal of intent, so refuse it outright
# rather than relying on the flag's absence.
FORBIDDEN_KEY_PREFIX = "pattern-where-python"

REQUIRED_KEYS = ("id", "message", "severity", "languages")

# At least one of these must be present for a rule to match anything.
PATTERN_KEYS = frozenset({
    "pattern", "patterns", "pattern-either", "pattern-regex",
    "pattern-sources", "pattern-sinks", "taint-sources", "taint-sinks",
})

VALID_SEVERITIES = frozenset({"ERROR", "WARNING", "INFO"})

MAX_YAML_BYTES = 8_000
RULE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")

# Per-rule ceiling inside semgrep, so one pathological pattern-regex cannot hang
# the review. The subprocess timeout in SemgrepTool is the outer bound.
SEMGREP_RULE_TIMEOUT_SECONDS = 10


@dataclass
class CustomRule:
    """One rule the agent wrote, and what we decided about it."""

    rule_id: str
    yaml_text: str
    rationale: str = ""
    valid: bool = False
    rejected_because: str = ""
    hits: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.rule_id,
            "rationale": self.rationale,
            "valid": self.valid,
            "rejected_because": self.rejected_because,
            "hits": self.hits,
            "yaml": self.yaml_text,
        }


@dataclass
class RuleCollector:
    """Sink for `write_rule`, mirroring FindingCollector."""

    max_rules: int = 10
    rules: list[CustomRule] = field(default_factory=list)

    @property
    def accepted(self) -> list[CustomRule]:
        return [r for r in self.rules if r.valid]

    def ids(self) -> set[str]:
        return {r.rule_id for r in self.rules if r.valid}

    def add(self, rule: CustomRule) -> None:
        self.rules.append(rule)

    def is_full(self) -> bool:
        return len(self.accepted) >= self.max_rules


def _find_forbidden(node) -> str | None:
    """Walk the parsed rule for semgrep's code-execution key, at any depth."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.strip().lower().startswith(FORBIDDEN_KEY_PREFIX):
                return key
            found = _find_forbidden(value)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_forbidden(item)
            if found:
                return found
    return None


def _extract_rule(parsed) -> tuple[dict | None, str]:
    """Accept either a bare rule mapping or a {'rules': [...]} document."""
    if not isinstance(parsed, dict):
        return None, "the rule must be a YAML mapping"

    if "rules" in parsed:
        rules = parsed["rules"]
        if not isinstance(rules, list) or len(rules) != 1:
            return None, "write exactly one rule per call, under a 'rules:' list"
        if not isinstance(rules[0], dict):
            return None, "the entry under 'rules:' must be a mapping"
        return rules[0], ""

    return parsed, ""


def validate_rule(
    yaml_text: str,
    collector: RuleCollector,
    workspace: str | None = None,
    run_semgrep_validate: bool = True,
) -> CustomRule:
    """Decide whether an authored rule may be run.

    Always returns a CustomRule; `valid` and `rejected_because` say what was
    decided. A rejection is fed back to the agent so it can correct, the same
    way fix validation works in `fixes.py`.
    """
    rule = CustomRule(rule_id="", yaml_text=yaml_text or "", rationale="")

    if not yaml_text or not yaml_text.strip():
        rule.rejected_because = "the rule was empty"
        return rule

    if len(yaml_text.encode("utf-8")) > MAX_YAML_BYTES:
        rule.rejected_because = (
            f"the rule is larger than {MAX_YAML_BYTES} bytes; write something tighter"
        )
        return rule

    if collector.is_full():
        rule.rejected_because = (
            f"already at the {collector.max_rules}-rule limit; keep only the "
            "checks that matter most for this repository"
        )
        return rule

    # safe_load, never load: load() constructs arbitrary Python objects.
    try:
        parsed = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        rule.rejected_because = f"invalid YAML: {str(e).splitlines()[0]}"
        return rule

    body, error = _extract_rule(parsed)
    if body is None:
        rule.rejected_because = error
        return rule

    forbidden = _find_forbidden(body)
    if forbidden:
        rule.rejected_because = (
            f"'{forbidden}' executes Python and is not allowed. Rules must be "
            "declarative patterns only."
        )
        logger.warning("Rejected an authored rule containing %s", forbidden)
        return rule

    missing = [k for k in REQUIRED_KEYS if k not in body]
    if missing:
        rule.rejected_because = f"missing required key(s): {', '.join(missing)}"
        return rule

    rule.rule_id = str(body["id"]).strip()
    if not RULE_ID_RE.match(rule.rule_id):
        rule.rejected_because = (
            f"rule id '{rule.rule_id}' must be 3-64 lowercase letters, digits or hyphens"
        )
        return rule

    if rule.rule_id in collector.ids():
        rule.rejected_because = f"rule id '{rule.rule_id}' is already used"
        return rule

    severity = str(body["severity"]).strip().upper()
    if severity not in VALID_SEVERITIES:
        rule.rejected_because = (
            f"severity must be one of {', '.join(sorted(VALID_SEVERITIES))}"
        )
        return rule

    if not any(key in body for key in PATTERN_KEYS):
        rule.rejected_because = (
            "the rule matches nothing; it needs at least one of: "
            + ", ".join(sorted(PATTERN_KEYS))
        )
        return rule

    languages = body.get("languages")
    if not isinstance(languages, list) or not languages:
        rule.rejected_because = "'languages' must be a non-empty list, e.g. [python]"
        return rule

    if run_semgrep_validate:
        error = _semgrep_validate(body, workspace)
        if error:
            rule.rejected_because = f"semgrep rejected the rule: {error}"
            return rule

    rule.valid = True
    rule.rejected_because = ""
    return rule


def _semgrep_validate(body: dict, workspace: str | None) -> str:
    """Let semgrep be the final authority on whether a rule is well formed."""
    with tempfile.NamedTemporaryFile(
        "w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as handle:
        yaml.safe_dump({"rules": [body]}, handle, sort_keys=False)
        path = handle.name

    try:
        proc = subprocess.run(
            # No --dangerously-* flag, here or anywhere else.
            ["semgrep", "--validate", "--config", path, "--quiet"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=workspace or None,
            check=False,
        )
    except FileNotFoundError:
        logger.info("semgrep not installed; skipping rule validation")
        return ""
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("semgrep --validate failed to run: %s", e)
        return ""
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    if proc.returncode == 0:
        return ""
    detail = (proc.stderr or proc.stdout or "").strip().splitlines()
    return detail[-1][:200] if detail else f"exit code {proc.returncode}"


def write_rules_file(rules: list[CustomRule], directory: str | None = None) -> str | None:
    """Combine accepted rules into one semgrep config.

    Written to a temp directory, never into the checkout — the action must not
    modify the repository it is reviewing.
    """
    accepted = [r for r in rules if r.valid]
    if not accepted:
        return None

    documents = []
    for rule in accepted:
        body, _ = _extract_rule(yaml.safe_load(rule.yaml_text))
        if body:
            documents.append(body)

    if not documents:
        return None

    target_dir = directory or tempfile.mkdtemp(prefix="pr-review-rules-")
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, "adaptive-rules.yaml")
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump({"rules": documents}, handle, sort_keys=False)

    logger.info("Wrote %d authored rule(s) to %s", len(documents), path)
    return path


def rejection_feedback(rule: CustomRule) -> str:
    """What to tell the agent when a rule cannot be run."""
    return (
        f"Rule rejected: {rule.rejected_because}\n"
        "Fix it and call write_rule again. Rules are declarative semgrep "
        "patterns — they cannot run code."
    )
