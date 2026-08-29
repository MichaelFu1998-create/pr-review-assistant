# Quickstart: Add PR Review to Your Repository

Five minutes. You need a repository, an API key, and permission to add a secret.

---

## Step 1 — Add your API key as a secret

In your repository: **Settings → Secrets and variables → Actions → New
repository secret**.

Add whichever your instructor gave you:

| Provider | Secret name | Get a key from |
|---|---|---|
| OpenAI | `OPENAI_API_KEY` | platform.openai.com |
| xAI (Grok) | `XAI_API_KEY` | console.x.ai |
| Anthropic | `ANTHROPIC_API_KEY` | console.anthropic.com |

> Never put a key in a workflow file. It becomes public the moment you push.

---

## Step 2 — Create the workflow

Create **`.github/workflows/pr-review.yaml`**:

```yaml
name: PR Review

on:
  pull_request:

permissions:
  contents: read
  pull-requests: write

concurrency:
  group: pr-review-${{ github.event.pull_request.number }}
  cancel-in-progress: true

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: MichaelFu1998-create/pr-review-assistant@v2
        with:
          github_token: ${{ secrets.GITHUB_TOKEN }}
          github_pr_id: ${{ github.event.pull_request.number }}
          openai_api_key: ${{ secrets.OPENAI_API_KEY }}
          agent_mode: single
```

Two details that are easy to miss:

- **`actions/checkout` is required, with `fetch-depth: 0`.** The reviewer reads
  your actual files and git history. Without a checkout it can only see the
  diff, and most tools will be skipped.
- **`pull-requests: write`** is what lets it post the review.

---

## Step 3 — Open a pull request

The review appears as comments within a minute or two. That's it.

---

## Choosing a mode

| `agent_mode` | What it does | Cost |
|---|---|---|
| `pipeline` | The original: one LLM call per file, no tools | Lowest |
| `single` | **Recommended.** One agent that investigates with tools | ~1× |
| `multi` | Parallel specialists per concern, then aggregation | ~4–8× |

Start with `single`. Use `multi` on a PR that matters. Use `pipeline` if you
want to see what the tool looked like before it was agentic.

---

## Using a different provider

**xAI (Grok):**

```yaml
with:
  github_token: ${{ secrets.GITHUB_TOKEN }}
  github_pr_id: ${{ github.event.pull_request.number }}
  llm_provider: xai
  xai_api_key: ${{ secrets.XAI_API_KEY }}
  openai_model: grok-4
```

**Anthropic:**

```yaml
with:
  github_token: ${{ secrets.GITHUB_TOKEN }}
  github_pr_id: ${{ github.event.pull_request.number }}
  llm_provider: anthropic
  anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
  openai_model: claude-sonnet-4-6
```

`openai_model` is the model name for every provider — the input keeps its old
name for backward compatibility.

---

## Common additions

**Learning-oriented reviews** (explains the principle behind each issue, adds a
0–25 score):

```yaml
  review_persona: mentor
  enable_scoring: "true"
```

**Security emphasis:**

```yaml
  review_persona: security-auditor
  review_focus: security
```

**Only review certain files:**

```yaml
  files: "src/**/*.py,src/**/*.ts"
  max_files: "20"
```

**Control the cost ceiling:**

```yaml
  max_agent_steps: "15"
  max_agent_tokens: "80000"
```

---

## Findings in the Security tab

Add SARIF output and one upload step:

```yaml
permissions:
  contents: read
  pull-requests: write
  security-events: write        # required

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: MichaelFu1998-create/pr-review-assistant@v2
        with:
          github_token: ${{ secrets.GITHUB_TOKEN }}
          github_pr_id: ${{ github.event.pull_request.number }}
          openai_api_key: ${{ secrets.OPENAI_API_KEY }}
          agent_mode: single
          output_sarif: pr-review.sarif

      - uses: github/codeql-action/upload-sarif@v3
        if: always()
        with:
          sarif_file: pr-review.sarif
```

Findings then appear under **Security → Code scanning**, tagged with their CWE
and tracked across runs.

---

## Saving a report for your records

```yaml
      - uses: MichaelFu1998-create/pr-review-assistant@v2
        with:
          # ...
          output_json: review.json

      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: review-report
          path: review.json
```

`review.json` contains every finding, counts by severity and category, the
scores, and the token cost of the run.

---

## Making the check fail

Off by default, on purpose — a false positive that blocks your branch is worse
than a missed warning.

```yaml
  fail_on: critical       # or: high, medium, low
```

`fail_on: high` fails on high **and** critical. It is a threshold, not a list.

---

## Repository-level defaults

Put settings that belong to the project in **`.pr-review.json`** at the repo
root, so every workflow doesn't repeat them:

```json
{
  "tools": {
    "enabled": ["semgrep", "bandit", "ruff"],
    "config": {
      "semgrep": { "rules": "p/owasp-top-ten" }
    }
  },
  "review": {
    "persona": "mentor",
    "focus": ["security", "quality"],
    "max_files": 15,
    "custom_instructions": "This project uses Django REST Framework.",
    "scoring": { "enabled": true }
  }
}
```

Precedence: **workflow input → `.pr-review.json` → built-in default.** An input
you leave out of the workflow falls through to the file.

---

## A note about forks

If your PR comes from a **fork**, GitHub does not give the workflow your
secrets, and the review will be skipped. This is intentional on GitHub's part:
otherwise anyone could open a PR that prints your API key.

For coursework this rarely matters — branches in your own repository work
normally.

**Do not "fix" this with `pull_request_target` plus a checkout of the PR head.**
That combination runs untrusted code with access to your secrets, and it is one
of the vulnerability classes this reviewer is built to catch.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| No review posted | Missing `pull-requests: write`, or the API key secret name doesn't match |
| "not set" error in the log | `llm_provider` doesn't match the key you supplied |
| Comments land on the wrong line | Missing `actions/checkout` — the reviewer can't see the files |
| "Repository not checked out" warning | Add `actions/checkout` before the action |
| Footer says **stopped early** | Budget hit; raise `max_agent_steps` or narrow `files` |
| Analysers skipped | Add `fetch-depth: 0` to checkout |
| Review is shallow | Try `agent_mode: multi`, or `review_focus` to concentrate it |

---

## Full reference

Every input is documented in [`action.yaml`](../action.yaml).
To understand what the reviewer is actually doing, read
[HOW_IT_WORKS.md](HOW_IT_WORKS.md).
