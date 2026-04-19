Testing the Action Locally
There are 3 approaches, from quickest to most realistic:

1. Direct Python invocation (fastest, test the logic)

# Set env vars to simulate GitHub Actions
export INPUT_OPENAI_API_KEY="sk-..."
export INPUT_GITHUB_TOKEN="ghp_..."
export INPUT_GITHUB_PR_ID="123"
export INPUT_OPENAI_MODEL="gpt-5.4-mini-2026-03-17"
export INPUT_TOOLS="none"          # skip tools for quick test
export INPUT_REVIEW_PERSONA="mentor"
export INPUT_LOGGING="debug"
export GITHUB_REPOSITORY="your-org/your-repo"
export GITHUB_WORKSPACE="."

cd /path/to/chatgpt-pr-review
pip install -r requirements.txt
python -m src.main
Use INPUT_TOOLS="none" to test the LLM review without needing analysis tools installed, or INPUT_TOOLS="ruff" if you have ruff installed locally.

2. Docker build + run (tests the full container)

# Build the image
docker build -t pr-review-test .

# Run against a real PR
docker run --rm \
  -e INPUT_OPENAI_API_KEY="sk-..." \
  -e INPUT_GITHUB_TOKEN="ghp_..." \
  -e INPUT_GITHUB_PR_ID="123" \
  -e INPUT_TOOLS="none" \
  -e INPUT_LOGGING="debug" \
  -e GITHUB_REPOSITORY="your-org/your-repo" \
  -e GITHUB_WORKSPACE="/workspace" \
  -v /path/to/checked-out-repo:/workspace \
  pr-review-test
3. act — run the full GitHub Actions workflow locally
act simulates GitHub Actions on your machine:


brew install act

# Create a .secrets file (don't commit this!)
echo "OPENAI_API_KEY=sk-..." > .secrets
echo "GITHUB_TOKEN=ghp_..." >> .secrets

# Run the workflow against a PR
act pull_request \
  --secret-file .secrets \
  -e '{"pull_request":{"number":123}}' \
  --container-architecture linux/amd64

Tips
Use INPUT_LOGGING="debug" to see full prompts, tool output, and token counts
Start with INPUT_TOOLS="none" to verify the LLM flow, then add tools incrementally
For testing tools without posting to GitHub, you can add a dry-run check in the code or just read the debug logs
Create a test PR on a throwaway repo to avoid spamming real PRs