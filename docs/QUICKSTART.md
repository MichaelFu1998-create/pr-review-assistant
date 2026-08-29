# Quickstart: Add PR Review to Your Repository

Five minutes. You need a repository, an API key, and permission to add a secret.

---

## Step 1 — Add your API key as a secret

In your repository: **Settings → Secrets and variables → Actions → New
repository secret**.

Add whichever your instructor gave you:

| Provider | Secret name | Get a key from |
|---|---|---|
| **xAI (Grok)** — default | `XAI_API_KEY` | console.x.ai |
| OpenAI | `OPENAI_API_KEY` | platform.openai.com |
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
          xai_api_key: ${{ secrets.XAI_API_KEY }}
```

That is the whole configuration. It defaults to `grok-4.6` in agent mode with
suggested fixes on.

Two details that are easy to miss:

- **`actions/checkout` is required, with `fetch-depth: 0`.** The reviewer reads
  your actual files and git history. Without a checkout it can only see the
  diff, and most tools will be skipped.
- **`pull-requests: write`** is what lets it post the review.

---

## Step 3 — Open a pull request

The review appears as comments within a minute or two. That's it.

---

## Applying suggested fixes

For issues it is confident about, the reviewer attaches a **suggested change**.
GitHub renders the buttons on these, not us — so it is worth knowing exactly what
each one does.

| Action | What it does |
|---|---|
| **Apply suggestion** | Accepts the fix and commits it to your branch |
| **Add suggestion to batch** | Collects several into one commit. **Only works in the Files changed tab** — the button appears in the Conversation tab but refuses to run there |
| **Resolve conversation** | Marks the thread handled. It applies nothing and discards nothing — it is just thread state. This is how you decline a fix |
| Do nothing | The suggestion is ignored |

There is no red "reject" button, because GitHub does not provide one. Declining a
fix means either resolving the thread or simply leaving it — the outcome is the
same.

### Merging does not apply anything

> If you merge the PR without clicking **Apply**, every suggestion is discarded.
> The branch merges exactly as it stands. Suggestions are review comments; they
> never modify your code on their own.

Apply the ones you want *before* you merge.

### Why some findings have no Apply button

Every comment tells you which of these it is:

| Heading | Meaning |
|---|---|
| **Suggested fix — apply directly** | A one-click fix. Applying it commits the change |
| **Suggested fix — apply by hand** | The reviewer proposed code, but it could not be turned into a suggestion. The reason is stated inline |
| **How to fix — manual change** | The correction needs a new import, dependency, or a restructure beyond the commented lines, so no line replacement can express it |

A missing button is not a rejected finding — it is still real feedback, it just
needs a human edit.

Note that **added lines can carry a suggestion**. What decides it is whether the
lines appear in the diff at all, not whether they were added or changed.

Turn suggestions off entirely with `suggest_fixes: "false"`.

## Choosing a mode

| `agent_mode` | What it does | Cost |
|---|---|---|
| `agent` | **Default.** An agent that investigates with tools and proposes fixes | ~1× |
| `pipeline` | The original v1 engine: one LLM call per file, no tools, no fixes | Lowest |

Use `pipeline` only if you want to see what the tool looked like before it was
agentic — it is kept for comparison.

---

## Using a different provider

**OpenAI:**

```yaml
with:
  github_token: ${{ secrets.GITHUB_TOKEN }}
  github_pr_id: ${{ github.event.pull_request.number }}
  llm_provider: openai
  openai_api_key: ${{ secrets.OPENAI_API_KEY }}
  openai_model: gpt-5.4-mini-2026-03-17
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

On `grok-4.6` you can also set `reasoning_effort` to `low`, `medium`, `high`, or
`xhigh`. It is the real depth-versus-cost dial for that model; leave it unset to
take the provider default.

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
          xai_api_key: ${{ secrets.XAI_API_KEY }}
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
| No Apply buttons | The fix lines were outside your diff, or the reviewer was not confident enough. Both are intentional |
| "Add suggestion to batch" does nothing | You are in the Conversation tab. Batching only works in **Files changed** |
| Merged, but the fixes are missing | Suggestions apply only when clicked. Merging discards unapplied ones |
| Review is shallow | Raise `max_agent_steps`, set `reasoning_effort: high`, or use `review_focus` |

---

## Full reference

Every input is documented in [`action.yaml`](../action.yaml).
To understand what the reviewer is actually doing, read
[HOW_IT_WORKS.md](HOW_IT_WORKS.md).
