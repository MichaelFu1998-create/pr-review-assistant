"""The agent loop.

Provider-agnostic: it speaks the normalised Message/ToolCall shapes from
``llm.base``, so the same loop drives OpenAI, xAI, and Anthropic.

The loop always terminates. It ends on `finish`, on budget exhaustion, or on a
model that has stopped making progress — and in every case it returns whatever
findings were collected, because a partial review is still worth posting.
"""

import logging
from dataclasses import dataclass, field

from ..llm.base import LLMConfig, LLMProvider, Message
from .budget import Budget
from .findings import AgentFinding, FindingCollector
from .toolbelt import Toolbelt

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    findings: list[AgentFinding] = field(default_factory=list)
    summary: str = ""
    scores: dict = field(default_factory=dict)
    budget: dict = field(default_factory=dict)
    stopped_because: str = "finished"


def run_agent(
    llm: LLMProvider,
    llm_config: LLMConfig,
    system_prompt: str,
    kickoff: str,
    toolbelt: Toolbelt,
    collector: FindingCollector,
    budget: Budget,
) -> AgentResult:
    """Drive one agent until it finishes or runs out of budget."""
    messages: list[Message] = [Message.user(kickoff)]
    schemas = toolbelt.schemas()

    summary = ""
    scores: dict = {}
    stopped = "finished"

    while True:
        if budget.exhausted():
            stopped = budget.stop_reason
            # Give the model one turn to summarise what it already found rather
            # than cutting off mid-investigation with no narrative at all.
            summary = summary or _final_summary(
                llm, llm_config, system_prompt, messages, budget
            )
            break

        try:
            response = llm.complete_with_tools(
                system_prompt, messages, schemas, llm_config
            )
        except Exception as e:
            logger.error("LLM call failed at step %d: %s", budget.steps, e)
            stopped = f"llm error: {e}"
            break

        budget.record_step(response.usage)

        if not response.wants_tools:
            # Text with no tool call: the model believes it is done but did not
            # say so. Take its prose as the summary rather than looping.
            summary = response.text.strip() or summary
            stopped = "model stopped without calling finish"
            logger.info("Agent ended without finish(); using its final message.")
            break

        messages.append(Message.assistant(response.text, response.tool_calls))

        finished = False
        for call in response.tool_calls:
            if not budget.record_call(call.name, call.arguments):
                messages.append(
                    Message.tool_result(
                        call.id,
                        call.name,
                        f"Error: you have called {call.name} with these exact "
                        "arguments repeatedly. Try a different approach, or call "
                        "finish if you have nothing left to investigate.",
                    )
                )
                continue

            outcome = toolbelt.dispatch(call)
            messages.append(Message.tool_result(call.id, call.name, outcome.text))

            if outcome.is_finish:
                payload = outcome.payload or {}
                summary = payload.get("summary", "")
                scores = payload.get("scores") or {}
                finished = True

        if finished:
            break

    return AgentResult(
        findings=collector.sorted(),
        summary=summary,
        scores=scores,
        budget=budget.summary(),
        stopped_because=stopped,
    )


def _final_summary(
    llm: LLMProvider,
    llm_config: LLMConfig,
    system_prompt: str,
    messages: list[Message],
    budget: Budget,
) -> str:
    """Ask for a closing summary after the budget ran out.

    Offered no tools, so it cannot start another investigation it cannot finish.
    """
    messages = messages + [
        Message.user(
            "You have reached your investigation budget. Do not call any more "
            "tools. Write a short markdown summary of the change and of what you "
            "found, and note explicitly that the review was cut short."
        )
    ]
    try:
        response = llm.complete_with_tools(system_prompt, messages, [], llm_config)
        budget.record_step(response.usage)
        return response.text.strip()
    except Exception as e:
        logger.warning("Could not get a closing summary: %s", e)
        return "_Review stopped early: investigation budget exhausted._"
