"""Single-agent review mode: one agent, one context, the full toolbelt."""

import logging

from ..config import Config
from ..llm.base import LLMConfig, LLMProvider
from ..tools.base import format_findings_for_prompt
from .budget import Budget
from .context import ReviewContext
from .findings import FindingCollector, merge_findings
from .loop import AgentResult, run_agent
from .prompts import build_kickoff_message, build_system_prompt
from .toolbelt import Toolbelt

logger = logging.getLogger(__name__)


def run_single_agent(
    llm: LLMProvider,
    llm_config: LLMConfig,
    config: Config,
    context: ReviewContext,
    budget: Budget | None = None,
) -> AgentResult:
    """Run one agent over the whole PR and return its merged findings."""
    budget = budget or Budget(
        max_steps=config.max_agent_steps,
        max_tokens=config.max_agent_tokens,
    )
    collector = FindingCollector(max_findings=config.max_findings)
    toolbelt = Toolbelt(
        context, collector, budget, source="agent",
        suggest_fixes=config.suggest_fixes,
    )

    system_prompt = build_system_prompt(config)
    kickoff = build_kickoff_message(
        manifest=context.manifest(),
        tool_summary=format_findings_for_prompt(context.tool_findings),
    )

    logger.info(
        "Starting single agent over %d file(s), budget: %d steps / %d tokens",
        len(context.changed_paths),
        budget.max_steps,
        budget.max_tokens,
    )

    result = run_agent(
        llm=llm,
        llm_config=llm_config,
        system_prompt=system_prompt,
        kickoff=kickoff,
        toolbelt=toolbelt,
        collector=collector,
        budget=budget,
    )

    # Fold in analyser findings the agent never spoke to, so a real hit is not
    # lost just because the agent ran out of budget before reaching it.
    result.findings = merge_findings(result.findings, context.tool_findings)

    logger.info(
        "Agent finished: %d finding(s), %s",
        len(result.findings),
        result.budget,
    )
    return result
