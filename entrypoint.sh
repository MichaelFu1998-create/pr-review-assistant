#!/bin/sh -l
# The checkout belongs to the runner user, but this action's container runs as
# root, so git refuses to touch it: "detected dubious ownership in repository".
# That silently breaks search_repo, find_symbol and git_log — the agent loses
# the ability to search the codebase and reports a generic tool error instead.
# Marking the workspace safe is the documented fix for containerised actions.
git config --global --add safe.directory "${GITHUB_WORKSPACE:-/github/workspace}" 2>/dev/null || true
git config --global --add safe.directory '*' 2>/dev/null || true

PYTHONPATH=/app python -m src.main
