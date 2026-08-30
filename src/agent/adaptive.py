"""Adaptive review mode: reconnoitre, author detectors, then review.

`agent` mode brings the same toolbelt to every repository, so it cannot know
that this project marks authorisation with `@requires_scope` or that money must
go through a specific type. This mode reads the repository first and writes
semgrep rules for what it finds, before the normal review begins.

The authored rules are declarative YAML validated in `rules.py`; nothing the
model writes is ever executed as code.
"""

import logging

from ..config import Config
from ..llm.base import LLMConfig, LLMProvider
from ..tools.base import format_findings_for_prompt
from ..tools.registry import discover_tools
from ..tools.runner import _run_single_tool
from .budget import Budget
from .context import ReviewContext
from .findings import CUSTOM_RULE_SOURCE, FindingCollector, merge_findings
from .loop import AgentResult, run_agent
from .prompts import AUTHORING_PROMPT, RECON_PROMPT, build_kickoff_message, build_system_prompt
from .rules import CustomRule, RuleCollector, write_rules_file
from .toolbelt import Toolbelt

logger = logging.getLogger(__name__)

# Recon and authoring are bounded tightly: they exist to inform the review, not
# to become it. The review pass keeps whatever budget remains.
RECON_STEPS = 10
AUTHORING_STEPS = 8

CUSTOM_TOOL_NAME = CUSTOM_RULE_SOURCE


def run_adaptive_agent(
    llm: LLMProvider,
    llm_config: LLMConfig,
    config: Config,
    context: ReviewContext,
    budget: Budget | None = None,
) -> AgentResult:
    """Recon, author rules, run them, then review with the results in hand."""
    budget = budget or Budget(
        max_steps=config.max_agent_steps,
        max_tokens=config.max_agent_tokens,
        max_seconds=config.max_agent_seconds,
    )

    brief = _recon(llm, llm_config, config, context, budget)
    rules = _author_rules(llm, llm_config, config, context, budget, brief)
    _run_custom_rules(rules, context)

    result = _review(llm, llm_config, config, context, budget)
    result.custom_rules = rules
    result.recon_brief = brief
    # Report the whole run, not just the review phase: the footer's step and
    # token counts must account for recon and authoring too.
    result.budget = budget.summary()
    return result


def _phase_budget(parent: Budget, steps: int) -> Budget:
    """A sub-budget that cannot outlive the parent's token allowance."""
    return Budget(
        max_steps=steps,
        max_tokens=max(parent.max_tokens - parent.tokens_used, 10_000),
        max_seconds=max(parent.max_seconds - parent.elapsed, 60),
        max_repeated_calls=parent.max_repeated_calls,
    )


def _absorb(parent: Budget, child: Budget) -> None:
    """Fold a phase's spend back into the run's total."""
    parent.steps += child.steps
    parent.usage = parent.usage + child.usage


def _recon(llm, llm_config, config: Config, context: ReviewContext, budget: Budget) -> str:
    """Phase 1 — characterise the repository. Reports nothing."""
    phase = _phase_budget(budget, RECON_STEPS)
    # No collector plumbing: recon is told not to report, and a finding filed
    # here would bypass the review pass that validates findings.
    collector = FindingCollector(max_findings=0)
    toolbelt = Toolbelt(context, collector, phase, source="recon", suggest_fixes=False)

    logger.info("Adaptive: recon over %d changed file(s)", len(context.changed_paths))
    result = run_agent(
        llm=llm,
        llm_config=llm_config,
        system_prompt=RECON_PROMPT,
        kickoff=(
            f"## Changed files\n\n{context.manifest()}\n\n"
            "Characterise this repository. Do not review it yet."
        ),
        toolbelt=toolbelt,
        collector=collector,
        budget=phase,
    )
    _absorb(budget, phase)
    logger.info("Adaptive: recon finished in %d step(s)", phase.steps)
    return result.summary.strip()


def _author_rules(
    llm, llm_config, config: Config, context: ReviewContext, budget: Budget, brief: str
) -> list[CustomRule]:
    """Phase 2 — write semgrep rules for this repository's own conventions."""
    if not brief:
        logger.info("Adaptive: recon produced no brief; skipping rule authoring")
        return []

    phase = _phase_budget(budget, AUTHORING_STEPS)
    rule_collector = RuleCollector(max_rules=config.max_custom_rules)
    collector = FindingCollector(max_findings=0)
    toolbelt = Toolbelt(
        context, collector, phase, source="authoring",
        suggest_fixes=False, rule_collector=rule_collector,
    )

    result = run_agent(
        llm=llm,
        llm_config=llm_config,
        system_prompt=AUTHORING_PROMPT,
        kickoff=(
            f"## What you found\n\n{brief}\n\n"
            f"## Changed files\n\n{context.manifest()}\n\n"
            "Write rules for this repository's own conventions."
        ),
        toolbelt=toolbelt,
        collector=collector,
        budget=phase,
    )
    _absorb(budget, phase)

    accepted = rule_collector.accepted
    logger.info(
        "Adaptive: authored %d rule(s), %d rejected — %s",
        len(accepted),
        len(rule_collector.rules) - len(accepted),
        result.summary[:120] if result.summary else "(no summary)",
    )
    return rule_collector.rules


def _run_custom_rules(rules: list[CustomRule], context: ReviewContext) -> None:
    """Run the accepted rules and fold their hits into the pre-pass findings."""
    path = write_rules_file(rules)
    if path is None:
        return

    registry = discover_tools()
    tool_class = registry.get("semgrep")
    if tool_class is None:
        logger.warning("semgrep is not registered; authored rules cannot run")
        return

    tool = tool_class()
    if not tool.is_available() and not tool.install():
        logger.warning("semgrep unavailable; authored rules cannot run")
        return

    files = tool.filter_files(context.changed_paths)
    if not files:
        return

    result = _run_single_tool(tool, files, context.workspace, {"rulesets": [path]})
    for error in result.errors:
        logger.warning("Authored rules: %s", error)

    by_id = {r.rule_id: r for r in rules if r.valid}
    for finding in result.findings:
        # Distinguish these from the standard rulesets in every report.
        finding.tool = CUSTOM_TOOL_NAME
        # Semgrep namespaces rule ids by the config file's path, so a rule
        # written to /tmp/pr-review-rules-xyz/adaptive-rules.yaml comes back as
        # "tmp.pr-review-rules-xyz.adaptive-rules.<id>". Strip that back to the
        # authored id: otherwise hit counts never match and the temp path leaks
        # into comments, SARIF, and the Security tab.
        short_id = finding.rule_id.rsplit(".", 1)[-1]
        matched = by_id.get(short_id)
        if matched:
            finding.rule_id = short_id
            matched.hits += 1

    if result.findings:
        context.record_analyzer_run(CUSTOM_TOOL_NAME, result.findings)
    logger.info(
        "Adaptive: authored rules produced %d finding(s)", len(result.findings)
    )


def _review(llm, llm_config, config: Config, context: ReviewContext, budget: Budget) -> AgentResult:
    """Phase 3 — the standard review, now with custom-rule hits as evidence.

    Gets its own step allowance rather than whatever recon and authoring left
    over. Steps bound how deep a single agent can go, so spending them on recon
    should not shorten the review; tokens are what bound the run's total cost,
    and those are still shared.
    """
    phase = _phase_budget(budget, config.max_agent_steps)
    collector = FindingCollector(max_findings=config.max_findings)
    toolbelt = Toolbelt(
        context, collector, phase, source="agent",
        suggest_fixes=config.suggest_fixes,
    )

    kickoff = build_kickoff_message(
        manifest=context.manifest(),
        tool_summary=format_findings_for_prompt(context.tool_findings),
    )
    if any(f.tool == CUSTOM_TOOL_NAME for f in context.tool_findings):
        # Without this the reviewer treats a custom-rule hit as "already
        # handled" and files nothing, then writes "Filed: ..." in its summary —
        # so the author sees a claim with no comment and no fix behind it.
        kickoff += (
            "\n\n## About the custom-rule hits\n\n"
            "Rules you wrote for this repository have already fired, and each "
            "hit is reported to the author on its own. You do not need to "
            "repeat one.\n\n"
            "But a rule can only point at a line. If you can add something a "
            "pattern cannot — why it matters here, the blast radius, or an "
            "applyable fix — then call `post_finding` at the same line. Yours "
            "supersedes the rule's.\n\n"
            "Whatever you decide, your summary must describe only what you "
            "actually filed. Do not write that you filed something you did not."
        )

    result = run_agent(
        llm=llm,
        llm_config=llm_config,
        system_prompt=build_system_prompt(config),
        kickoff=kickoff,
        toolbelt=toolbelt,
        collector=collector,
        budget=phase,
    )
    _absorb(budget, phase)
    result.findings = merge_findings(result.findings, context.tool_findings)
    return result
