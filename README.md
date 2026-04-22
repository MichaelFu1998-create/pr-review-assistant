# PR Review Assistant

Automated PR code review powered by LLM + static analysis tools. Supports multiple languages, review personas, educational scoring, and flexible configuration for diverse tech stacks.

## Features

- **Static analysis integration** — Semgrep, Ruff, Bandit, ESLint, detect-secrets, and more run locally and feed findings into the LLM for context-aware reviews
- **Auto-detect tech stack** — Automatically selects relevant tools based on your project's languages
- **Review personas** — Choose between `normal` (balanced), `mentor` (educational), or `security-auditor` modes. All personas share the same standardized 8-category defect checklist, so results are directly comparable; persona only changes tone and emphasis.
- **Token management** — Smart truncation ensures large files and tool outputs fit within model context windows
- **Educational scoring** — Optional 0-25 rubric scoring for capstone/course use
- **Multi-provider LLM support** — OpenAI, Anthropic Claude, or any OpenAI-compatible API
- **Parameterizable** — Per-repo `.pr-review.json` config for fine-grained control
- **11 tool plugins** — Semgrep, Ruff, Bandit, ESLint, npm audit, PMD, Checkstyle, golangci-lint, Hadolint, ShellCheck, Trivy, Checkov, detect-secrets

## Quick Start

### 1. Set up secrets

- `OPENAI_API_KEY` — from [platform.openai.com](https://platform.openai.com)
- `GITHUB_TOKEN` — ensure [write permissions](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository#configuring-the-default-github_token-permissions) for pull requests

### 2. Create workflow

Create `.github/workflows/pr-review.yaml`:

```yaml
name: PR Code Review
on:
  pull_request:
    types: [opened, synchronize]
jobs:
  review:
    name: Automated Code Review
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: MichaelFu1998-create/pr-review-assistant@v1.0.0
        with:
          openai_api_key: ${{ secrets.OPENAI_API_KEY }}
          github_token: ${{ secrets.GITHUB_TOKEN }}
          github_pr_id: ${{ github.event.number }}
```

> **Important:** `actions/checkout` is required before this action when using static analysis tools (the default).

### 3. (Optional) Customize with `.pr-review.json`

Place in your repo root:

```json
{
  "tools": {
    "enabled": ["semgrep", "ruff", "detect_secrets"],
    "config": {
      "semgrep": { "rulesets": ["p/python", "p/security-audit"] },
      "ruff": { "select": ["E", "F", "B", "S", "C90"] }
    }
  },
  "review": {
    "focus": ["security", "quality"],
    "persona": "mentor",
    "custom_instructions": "We use Django REST Framework. Pay attention to serializer validation."
  }
}
```

## All Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `openai_api_key` | Yes | — | OpenAI API key |
| `github_token` | Yes | — | GitHub token with PR write access |
| `github_pr_id` | Yes | — | PR number to review |
| `openai_model` | No | `gpt-5.4-mini-2026-03-17` | LLM model name |
| `openai_temperature` | No | `1` | Sampling temperature [0, 1] |
| `openai_max_tokens` | No | `32000` | Max response tokens |
| `llm_provider` | No | `openai` | `openai`, `anthropic` |
| `api_base_url` | No | — | Custom API URL (Ollama, vLLM, Azure) |
| `anthropic_api_key` | No | — | Anthropic key (when using Claude) |
| `files` | No | `*` | Comma-separated glob patterns |
| `max_files` | No | `10` | Max files per review |
| `tools` | No | `auto` | `auto`, `none`, or tool list |
| `severity_threshold` | No | `low` | Min severity: critical/high/medium/low/info |
| `review_focus` | No | `all` | Focus: security/quality/performance/education/all |
| `review_persona` | No | `normal` | Style: normal/mentor/security-auditor |
| `custom_instructions` | No | — | Additional LLM instructions |
| `enable_scoring` | No | `false` | Enable 0-25 educational rubric |
| `logging` | No | `warning` | Log level |

## Static Analysis Tools

### Auto-Detection

When `tools: "auto"` (default), tools are selected based on detected languages:

| Language | Tools |
|----------|-------|
| Python | Semgrep, Ruff, Bandit |
| JavaScript/TypeScript | Semgrep, ESLint |
| Java | Semgrep, PMD, Checkstyle |
| Go | Semgrep, golangci-lint |
| Shell | ShellCheck |
| Dockerfile | Hadolint |
| Terraform/IaC | Checkov |
| All languages | detect-secrets |

### Tool Tiers

**Tier 1 (pre-installed, instant):** Semgrep, Ruff, detect-secrets

**Tier 2 (installed on-demand, ~10-30s):** ESLint, Bandit, PMD, Checkstyle, golangci-lint, Hadolint, ShellCheck, Trivy, Checkov

### Disabling Tools

To use LLM-only review (no static analysis):

```yaml
tools: "none"
```

## Review Personas

| Persona | Style | Best For |
|---------|-------|----------|
| `normal` | Balanced, professional, category + severity per issue | General-purpose reviews (default) |
| `mentor` | Educational, explains WHY, encouraging | Students, capstone projects |
| `security-auditor` | CWE categories, risk levels | Security reviews |

> **All personas share the same standardized 8-category defect checklist** (Documentation, Visual Representation, Structure, New Functionality, Resource, Check, Interface, Logic defects). The persona controls tone and emphasis only — the checklist coverage is identical across modes, so results are directly comparable. See [IMPLEMENTATION.md](IMPLEMENTATION.md) for the full checklist and prompt pipeline.

## Educational Scoring

Enable with `enable_scoring: "true"`. The LLM scores each PR on:

- **Code Quality** (0-5): readability, naming, structure, DRY
- **Security** (0-5): input validation, auth, data protection
- **Testing** (0-5): coverage, edge cases, test quality
- **Documentation** (0-5): comments, docstrings, PR description
- **Architecture** (0-5): separation of concerns, design patterns

**Total: 0-25**

## Using with Different LLM Providers

### OpenAI (default)

```yaml
openai_api_key: ${{ secrets.OPENAI_API_KEY }}
openai_model: "gpt-5.4-mini-2026-03-17"
```

### Anthropic Claude

```yaml
llm_provider: "anthropic"
anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
openai_model: "claude-sonnet-4-6"
```

### OpenAI-Compatible (Ollama, vLLM, Azure)

```yaml
openai_api_key: "not-needed"
api_base_url: "http://localhost:11434/v1"
openai_model: "llama3"
```

## Examples

### Python + Security Focus

```yaml
- uses: MichaelFu1998-create/pr-review-assistant@v1.0.0
  with:
    openai_api_key: ${{ secrets.OPENAI_API_KEY }}
    github_token: ${{ secrets.GITHUB_TOKEN }}
    github_pr_id: ${{ github.event.number }}
    files: "*.py"
    tools: "semgrep,ruff,bandit"
    review_focus: "security"
    review_persona: "security-auditor"
```

### Full-Stack JS/TS

```yaml
- uses: MichaelFu1998-create/pr-review-assistant@v1.0.0
  with:
    openai_api_key: ${{ secrets.OPENAI_API_KEY }}
    github_token: ${{ secrets.GITHUB_TOKEN }}
    github_pr_id: ${{ github.event.number }}
    files: "*.js,*.ts,*.jsx,*.tsx"
    tools: "semgrep,eslint,npm_audit"
```

### Capstone Course (with scoring)

```yaml
- uses: MichaelFu1998-create/pr-review-assistant@v1.0.0
  with:
    openai_api_key: ${{ secrets.OPENAI_API_KEY }}
    github_token: ${{ secrets.GITHUB_TOKEN }}
    github_pr_id: ${{ github.event.number }}
    review_persona: "mentor"
    review_focus: "all"
    enable_scoring: "true"
```

### LLM-Only Review (no tools)

```yaml
- uses: MichaelFu1998-create/pr-review-assistant@v1.0.0
  with:
    openai_api_key: ${{ secrets.OPENAI_API_KEY }}
    github_token: ${{ secrets.GITHUB_TOKEN }}
    github_pr_id: ${{ github.event.number }}
    tools: "none"
```

## `.pr-review.json` Reference

```json
{
  "tools": {
    "enabled": ["semgrep", "ruff", "detect_secrets", "eslint"],
    "config": {
      "semgrep": {
        "rulesets": ["p/default", "p/python"],
        "severity": "WARNING"
      },
      "ruff": {
        "select": ["E", "F", "B", "S", "C90"],
        "line-length": 120
      },
      "eslint": {
        "config_path": ".eslintrc.json"
      }
    }
  },
  "review": {
    "focus": ["security", "quality"],
    "persona": "mentor",
    "severity_threshold": "medium",
    "max_files": 15,
    "custom_instructions": "Additional context for the reviewer",
    "scoring": {
      "enabled": true
    }
  }
}
```

**Priority:** Action inputs > `.pr-review.json` > Defaults

## Architecture

```
src/
  main.py              # Orchestrator
  config.py            # Config from env vars + .pr-review.json
  github_client.py     # GitHub API interactions
  llm/                 # LLM provider abstraction
    openai_provider.py
    anthropic_provider.py
  prompt/              # Prompt construction + token management
    builder.py
    templates.py
  tools/               # Static analysis framework
    base.py            # BaseTool interface + Finding dataclass
    registry.py        # Auto-discovers analyzer plugins
    runner.py          # Parallel execution engine
    stack_detector.py  # Tech stack auto-detection
    analyzers/         # Drop-in tool plugins
  checks/              # Quality checks
    pr_quality.py
    test_coverage.py
    git_hygiene.py
  review/              # Output formatting
    formatter.py
    scoring.py
```

### Adding a New Tool

Create a file in `src/tools/analyzers/` that subclasses `BaseTool`:

```python
from ..base import BaseTool, Finding, ToolResult

class MyTool(BaseTool):
    name = "mytool"
    languages = ["python"]
    category = "quality"
    install_cmd = "pip install mytool"

    def is_available(self) -> bool: ...
    def install(self) -> bool: ...
    def run(self, files, workspace, config) -> ToolResult: ...
```

The registry auto-discovers it. No other code changes needed.

## Pull Request Template

Optional: copy to `.github/PULL_REQUEST_TEMPLATE.md`:

```markdown
## Description
Summary of changes and motivation. Fixes # (issue number).

## Type of change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## How Has This Been Tested?
Describe tests run to verify changes.

## Checklist
- [ ] Code follows project style guidelines
- [ ] Adequate tests added
- [ ] No new warnings/errors generated
```

## Acknowledgements

This project is built upon the excellent foundation of [chatgpt-pr-review](https://github.com/agogear/chatgpt-pr-review) by [agogear](https://github.com/agogear). Many thanks for the original work that made this tool possible.
