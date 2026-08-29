"""Multi-agent review: parallel specialists, then one aggregation pass.

Costs several times a single-agent run, so it is opt-in. What it buys is depth:
each specialist reasons in its own context with one mandate, instead of one
context trying to hold ten review domains at once.
"""

import logging
from concurrent.futures import ThreadPoolExecutor

from ..config import Config
from ..llm.base import LLMConfig, LLMProvider, Message
from ..tools.base import format_findings_for_prompt
from .budget import Budget
from .context import ReviewContext
from .findings import AgentFinding, FindingCollector, merge_findings
from .loop import AgentResult, run_agent
from .prompts import build_kickoff_message, build_system_prompt
from .specialists import Specialist, select_specialists
from .toolbelt import Toolbelt

logger = logging.getLogger(__name__)

MAX_PARALLEL_SPECIALISTS = 4


def run_multi_agent(
    llm: LLMProvider,
    llm_config: LLMConfig,
    config: Config,
    context: ReviewContext,
    budget: Budget | None = None,
) -> AgentResult:
    """Run the applicable specialists in parallel and aggregate their findings."""
    budget = budget or Budget(
        max_steps=config.max_agent_steps,
        max_tokens=config.max_agent_tokens,
        max_seconds=config.max_agent_seconds,
    )

    specialists = select_specialists(context, config.specialists_list or None)
    if not specialists:
        logger.warning("No specialists selected; falling back to a single agent.")
        from .single import run_single_agent

        return run_single_agent(llm, llm_config, config, context, budget)

    logger.info(
        "Running %d specialist(s): %s",
        len(specialists),
        ", ".join(s.name for s in specialists),
    )

    tool_summary = format_findings_for_prompt(context.tool_findings)
    kickoff = build_kickoff_message(context.manifest(), tool_summary)

    results: dict[str, AgentResult] = {}
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_SPECIALISTS) as executor:
        futures = {
            executor.submit(
                _run_specialist,
                specialist,
                llm,
                llm_config,
                config,
                context,
                budget.split(len(specialists)),
                kickoff,
            ): specialist
            for specialist in specialists
        }
        for future in futures:
            specialist = futures[future]
            try:
                results[specialist.name] = future.result()
            except Exception as e:
                # One specialist failing must not lose the others' work.
                logger.warning("Specialist %s failed: %s", specialist.name, e)

    combined = _combine(results)
    combined = merge_findings(combined, context.tool_findings)

    # Summarise first, so the aggregation call's own tokens are counted.
    summary = _summarise(llm, llm_config, config, context, combined, results, budget)
    totals = _aggregate_budgets(results, budget)
    scores = _merge_scores(results)

    logger.info(
        "Multi-agent review complete: %d finding(s) from %d specialist(s), %d tokens",
        len(combined),
        len(results),
        totals["total_tokens"],
    )

    return AgentResult(
        findings=combined,
        summary=summary,
        scores=scores,
        budget=totals,
        stopped_because="finished",
    )


def _run_specialist(
    specialist: Specialist,
    llm: LLMProvider,
    llm_config: LLMConfig,
    config: Config,
    context: ReviewContext,
    budget: Budget,
    kickoff: str,
) -> AgentResult:
    collector = FindingCollector(max_findings=config.max_findings)
    toolbelt = Toolbelt(context, collector, budget, source=f"agent:{specialist.name}")
    system_prompt = build_system_prompt(
        config, extra=f"## Your mandate\n\n{specialist.mandate}"
    )
    logger.info("Specialist %s starting (%d steps)", specialist.name, budget.max_steps)
    return run_agent(
        llm=llm,
        llm_config=llm_config,
        system_prompt=system_prompt,
        kickoff=kickoff,
        toolbelt=toolbelt,
        collector=collector,
        budget=budget,
    )


def _combine(results: dict[str, AgentResult]) -> list[AgentFinding]:
    """Flatten specialist findings, dropping cross-specialist duplicates.

    Two specialists reaching the same place from different angles is common —
    a missing input check is both a security and a correctness finding. Keep
    the more severe report of the two.
    """
    best: dict[tuple, AgentFinding] = {}
    for result in results.values():
        for finding in result.findings:
            key = finding.dedup_key
            existing = best.get(key)
            if existing is None or finding.severity_rank < existing.severity_rank:
                best[key] = finding
    return sorted(
        best.values(),
        key=lambda f: (f.severity_rank, f.path, f.line if f.line is not None else 0),
    )


def _aggregate_budgets(results: dict[str, AgentResult], parent: Budget) -> dict:
    # parent carries the aggregation pass; results carry the specialists.
    totals = {
        "steps": sum(r.budget.get("steps", 0) for r in results.values()) + parent.steps,
        "prompt_tokens": sum(r.budget.get("prompt_tokens", 0) for r in results.values())
        + parent.usage.prompt_tokens,
        "completion_tokens": sum(
            r.budget.get("completion_tokens", 0) for r in results.values()
        )
        + parent.usage.completion_tokens,
        "total_tokens": sum(r.budget.get("total_tokens", 0) for r in results.values())
        + parent.tokens_used,
        "elapsed_seconds": round(parent.elapsed, 1),
        "specialists": sorted(results),
    }
    cut_short = [
        f"{name}: {r.stopped_because}"
        for name, r in results.items()
        if r.stopped_because not in ("finished", "model stopped without calling finish")
    ]
    totals["stop_reason"] = "; ".join(cut_short) if cut_short else "completed"
    return totals


def _merge_scores(results: dict[str, AgentResult]) -> dict:
    """Average each score across the specialists that offered one."""
    gathered: dict[str, list[int]] = {}
    for result in results.values():
        for key, value in (result.scores or {}).items():
            if isinstance(value, (int, float)):
                gathered.setdefault(key, []).append(int(value))
    return {k: round(sum(v) / len(v)) for k, v in gathered.items() if v}


def _summarise(
    llm: LLMProvider,
    llm_config: LLMConfig,
    config: Config,
    context: ReviewContext,
    findings: list[AgentFinding],
    results: dict[str, AgentResult],
    budget: Budget,
) -> str:
    """One pass to turn several specialist reports into a single verdict.

    Given no tools, so it cannot start a new investigation; its job is to
    reconcile what has already been found.
    """
    per_specialist = "\n\n".join(
        f"### {name}\n{result.summary or '(no summary)'}"
        for name, result in sorted(results.items())
    )
    finding_list = "\n".join(
        f"- [{f.severity}] {f.category} {f.path}:{f.line or '?'} — {f.title}"
        for f in findings[:60]
    ) or "(none)"

    prompt = (
        f"{len(results)} specialist reviewers examined this pull request.\n\n"
        f"## Changed files\n\n{context.manifest()}\n\n"
        f"## Their summaries\n\n{per_specialist}\n\n"
        f"## Combined findings\n\n{finding_list}\n\n"
        "Write the single review summary the author will read first. State what "
        "the change does, your overall assessment, and the two or three things "
        "that most need attention. Say what is done well. Where specialists "
        "disagree, resolve it and say which reading you accept. Do not list "
        "every finding again — they appear as inline comments. Markdown, no "
        "heading above level 3."
    )

    try:
        response = llm.complete_with_tools(
            build_system_prompt(config), [Message.user(prompt)], [], llm_config
        )
        budget.record_step(response.usage)
        return response.text.strip()
    except Exception as e:
        logger.warning("Aggregation summary failed: %s", e)
        names = ", ".join(sorted(results))
        return f"_Reviewed by {len(results)} specialists ({names})._"
