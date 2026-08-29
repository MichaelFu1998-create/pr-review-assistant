"""Main orchestrator — ties together all components."""

import logging
import os
import sys
from urllib.parse import quote

from .config import Config, load_config
from .github_client import (
    get_repo_and_pull,
    files_for_review,
    fetch_contextual_info,
    get_file_content,
    safe_create_review,
)
from .llm.base import LLMConfig
from .prompt.builder import build_prompt
from .review.formatter import format_review_comment, format_review_body
from .tools.base import format_findings_for_prompt
from .tools.registry import get_tools_for_config
from .tools.runner import run_tools
from .tools.stack_detector import detect_stack
from .checks.pr_quality import check_pr_quality
from .checks.test_coverage import analyze_test_coverage
from .checks.git_hygiene import check_git_hygiene
from .github_client import fetch_pr_metadata
from .diff.patch import DiffMap

logger = logging.getLogger(__name__)


# Config field (and action input name — they match) holding each provider's key.
PROVIDER_KEYS = {
    "openai": "openai_api_key",
    "anthropic": "anthropic_api_key",
    "xai": "xai_api_key",
}


def create_llm_provider(config: Config):
    """Create the appropriate LLM provider based on configuration."""
    if config.llm_provider == "anthropic":
        from .llm.anthropic_provider import AnthropicProvider
        return AnthropicProvider(api_key=config.anthropic_api_key)
    if config.llm_provider == "xai":
        from .llm.xai_provider import XAIProvider
        return XAIProvider(
            api_key=config.xai_api_key,
            base_url=config.api_base_url or None,
        )
    from .llm.openai_provider import OpenAIProvider
    return OpenAIProvider(
        api_key=config.openai_api_key,
        base_url=config.api_base_url or None,
    )


def validate_provider_key(config: Config) -> str | None:
    """Return an error message if the configured provider has no API key."""
    if config.llm_provider not in PROVIDER_KEYS:
        return (
            f"Unknown llm_provider '{config.llm_provider}'. "
            f"Expected one of: {', '.join(sorted(PROVIDER_KEYS))}"
        )
    key_field = PROVIDER_KEYS[config.llm_provider]
    if not getattr(config, key_field, ""):
        return f"llm_provider is '{config.llm_provider}' but '{key_field}' is not set"
    return None


def main():
    """Entry point. Dispatches to the v1 pipeline or the v2 agent."""
    config = load_config()

    # Setup logging
    logging.basicConfig(
        encoding="utf-8",
        level=getattr(logging, config.logging_level.upper(), logging.WARNING),
        format="%(levelname)s: %(name)s: %(message)s",
    )

    key_error = validate_provider_key(config)
    if key_error:
        logger.error(key_error)
        sys.exit(1)
    if not config.github_token:
        logger.error("GitHub token is required")
        sys.exit(1)
    if not config.github_pr_id:
        logger.error("GitHub PR ID is required")
        sys.exit(1)

    # Initialize LLM provider
    llm = create_llm_provider(config)
    llm_config = LLMConfig(
        model=config.model,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        reasoning_effort=config.reasoning_effort,
    )

    # Initialize GitHub
    repo_name = os.getenv("GITHUB_REPOSITORY", "")
    repo, pull = get_repo_and_pull(config.github_token, repo_name, config.github_pr_id)

    # Collect files for review
    files = files_for_review(pull, config.file_patterns)
    n_files = len(files)
    if n_files == 0:
        logger.info("No files to review")
        return
    if n_files > config.max_files:
        logger.error(
            f"Too many files to review ({n_files}), limit is {config.max_files}. "
            "Use the 'files' input to target specific files."
        )
        sys.exit(1)

    logger.info(f"Reviewing {n_files} file(s)")

    if config.agent_mode != "pipeline":
        run_agent_review(config, llm, llm_config, repo, pull, files, repo_name)
        return

    run_pipeline_review(config, llm, llm_config, repo, pull, files)


def run_pipeline_review(config, llm, llm_config, repo, pull, files):
    """The v1 review path, unchanged: one single-shot LLM call per file."""
    n_files = len(files)

    # Fetch PR context
    pr_description, pr_comments, readme = fetch_contextual_info(pull, repo)

    # Run static analysis tools
    workspace = os.environ.get("GITHUB_WORKSPACE", ".")
    changed_filenames = list(files.keys())
    tool_findings_map: dict[str, str] = {}  # filename -> formatted findings
    tools_used: list[str] = []
    total_findings = 0

    if config.tools != "none":
        # Detect tech stack
        detected_languages = detect_stack(changed_filenames, workspace)
        logger.info(f"Detected languages: {detected_languages}")

        # Get tools to run
        selected_tools = get_tools_for_config(
            detected_languages, config.tools, config.tools_list or None,
        )
        tools_used = [t.name for t in selected_tools]

        if selected_tools:
            # Validate workspace has repo checkout
            if not os.path.exists(os.path.join(workspace, ".git")):
                logger.warning(
                    "Repository not checked out in workspace. "
                    "Static analysis tools require 'actions/checkout' before this action. "
                    "Skipping tool analysis."
                )
            else:
                # Run all tools in parallel
                all_findings = run_tools(
                    selected_tools, changed_filenames, workspace,
                    config.tool_configs, config.severity_threshold,
                )
                total_findings = len(all_findings)
                logger.info(f"Total findings from tools: {total_findings}")

                # Group findings by file
                for finding in all_findings:
                    if finding.file not in tool_findings_map:
                        tool_findings_map[finding.file] = []
                    tool_findings_map[finding.file].append(finding)

                # Format findings per file
                tool_findings_map = {
                    filename: format_findings_for_prompt(findings_list)
                    for filename, findings_list in tool_findings_map.items()
                }

    # Run quality checks
    quality_observations = check_pr_quality(pull, n_files)
    test_observations = analyze_test_coverage(changed_filenames, workspace)
    hygiene_observations = check_git_hygiene(pull, files, workspace)

    # Review each file
    comments = []
    first_file = True

    for filename, commit_info in files.items():
        commit_sha = commit_info["sha"]
        content = get_file_content(repo, filename, commit_sha)
        if not content:
            logger.info(f"Skipping {filename}: empty or unreadable")
            continue

        # Build prompt with tool findings for this file
        file_findings = tool_findings_map.get(filename, "")
        prompt = build_prompt(
            filename, content, pr_description, pr_comments, readme,
            file_findings, config, llm,
        )

        logger.info(f"Reviewing {filename} ({prompt.total_tokens} tokens)")

        # Call LLM
        try:
            llm_review = llm.complete(
                prompt.system_message, prompt.user_message, llm_config,
            )
        except Exception as e:
            logger.error(f"LLM review failed for {filename}: {e}")
            continue

        # Format comment
        body = format_review_comment(
            filename,
            llm_review,
            tool_summary=file_findings if file_findings else None,
            quality_observations=quality_observations if first_file else None,
            test_observations=test_observations if first_file else None,
            hygiene_observations=hygiene_observations if first_file else None,
        )
        first_file = False

        comments.append({
            "path": quote(filename, safe="/"),
            "position": 1,
            "body": body,
        })

    # Post review (with fallback to a summary review if GitHub rejects the
    # inline comments — e.g. unresolvable paths or rate-limited submissions).
    if comments:
        review_body = format_review_body(len(comments), tools_used, total_findings)
        safe_create_review(pull, review_body, comments)
    else:
        logger.info("No review comments to post")



def run_prepass(config: Config, files: dict, workspace: str):
    """Run the deterministic analysers before the agent starts.

    The agent begins with these findings for free and can request more with
    run_analyzer. Returns (findings, tool_names).
    """
    if config.tools == "none":
        return [], []

    changed = list(files.keys())
    detected = detect_stack(changed, workspace)
    logger.info(f"Detected languages: {detected}")

    selected = get_tools_for_config(detected, config.tools, config.tools_list or None)
    if not selected:
        return [], []

    if not os.path.exists(os.path.join(workspace, ".git")):
        logger.warning(
            "Repository not checked out in workspace. Static analysis and the "
            "agent's file tools require 'actions/checkout' before this action."
        )
        return [], [t.name for t in selected]

    findings = run_tools(
        selected, changed, workspace, config.tool_configs, config.severity_threshold,
    )
    logger.info(f"Pre-pass produced {len(findings)} finding(s)")
    return findings, [t.name for t in selected]


def run_agent_review(config: Config, llm, llm_config, repo, pull, files, repo_name):
    """The v2 agentic path: investigate with tools, emit structured findings."""
    from .agent.budget import Budget
    from .agent.context import PRMetadata, ReviewContext
    from .agent.single import run_single_agent
    from .output.comments import build_inline_comments, split_by_source
    from .output.gating import should_fail
    from .output.json_report import build_report, write_report
    from .output.sarif import write_sarif
    from .output.summary import build_review_body
    from . import __version__

    workspace = os.environ.get("GITHUB_WORKSPACE", ".")
    tool_findings, tools_used = run_prepass(config, files, workspace)

    metadata = PRMetadata(**fetch_pr_metadata(pull))
    context = ReviewContext(
        workspace=workspace,
        diff=DiffMap.from_pull_files(files),
        metadata=metadata,
        tool_findings=list(tool_findings),
        tools_used=list(tools_used),
    )

    budget = Budget(
        max_steps=config.max_agent_steps,
        max_tokens=config.max_agent_tokens,
        max_seconds=config.max_agent_seconds,
    )

    result = run_single_agent(llm, llm_config, config, context, budget)

    # The non-LLM checks still run; they are cheap and catch things the agent
    # has no reason to look for.
    changed = list(files.keys())
    observations = {
        "PR Quality": check_pr_quality(pull, len(files)),
        "Test Coverage": analyze_test_coverage(changed, workspace),
        "Git Hygiene": check_git_hygiene(pull, files, workspace),
    }

    # Only the agent's findings become inline comments. Analyser hits it already
    # validated and re-reported would otherwise comment twice on the same line.
    agent_findings, analyser_findings = split_by_source(result.findings)
    comments, unanchored = build_inline_comments(agent_findings, context.diff)

    pr_url = (
        f"https://github.com/{repo_name}/pull/{config.github_pr_id}"
        if repo_name and config.github_pr_id
        else ""
    )
    review_body = build_review_body(
        findings=agent_findings,
        summary=result.summary,
        scores=result.scores,
        unanchored=unanchored,
        tools_used=context.tools_used,
        budget=result.budget,
        agent_mode=config.agent_mode,
        model=config.model,
        observations=observations,
        analyser_findings=analyser_findings,
        pr_url=pr_url,
        # Only when SARIF is actually uploaded; otherwise the note would
        # describe a check that does not exist.
        sarif_enabled=bool(config.output_sarif),
    )

    if comments or result.findings or result.summary:
        safe_create_review(pull, review_body, comments)
    else:
        logger.info("Nothing to report")

    if config.output_sarif:
        # Agent findings only, matching the inline comments. Uploading raw
        # analyser output would file security alerts for hits the agent
        # explicitly judged false positives — a pytest `assert` (ruff S101) or a
        # long line (E501) — and GitHub re-posts every alert as its own PR
        # comment, duplicating the review.
        path = write_sarif(agent_findings, config.output_sarif, tool_version=__version__)
        logger.info(f"Wrote SARIF to {path}")

    if config.output_json:
        # The report is an analytics record rather than a user-facing surface,
        # so it keeps both streams; totals.by_source separates them.
        report = build_report(
            result.findings,
            summary=result.summary,
            scores=result.scores,
            pr_number=config.github_pr_id,
            repository=repo_name,
            agent_mode=config.agent_mode,
            model=config.model,
            provider=config.llm_provider,
            tools_used=context.tools_used,
            budget=result.budget,
        )
        write_report(report, config.output_json)
        logger.info(f"Wrote JSON report to {config.output_json}")

    # Gate on the agent's validated findings, for the same reason SARIF does:
    # a merge should not be blocked by an analyser hit the agent judged a false
    # positive. ruff's S101 on a pytest `assert` is reported as high severity.
    failed, reason = should_fail(agent_findings, config.fail_on)
    if failed:
        logger.error(reason)
        sys.exit(1)


if __name__ == "__main__":
    main()
