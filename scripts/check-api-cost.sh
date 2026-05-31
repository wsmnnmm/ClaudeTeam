#!/usr/bin/env bash
# PreToolUse Hook — API cost guard for Claude Code agents.
#
# Called before every Bash tool invocation. Reads the command from stdin
# (Claude Code passes it via the hook protocol), pipes it to the Python
# cost-guard checker, and returns the exit code.
#
# Exit codes:
#   0 — proceed (no paid API detected, or warning only)
#   1 — blocked (session budget exceeded)
#
# To set the session budget:
#   export CLAUDETEAM_API_BUDGET_USD=10.0
#
# To reset the budget tracker:
#   claudeteam api-budget reset [--limit 10.0]
#
# Hooks live in ~/.claude/hooks/ and are activated via .claude/settings.json.
set -euo pipefail

# Resolve the claudeteam venv Python for the cost-guard module.
# Falls back to system python3 if venv not found.
_CT_VENV_PYTHON=""
for _candidate in \
    "$CLAUDETEAM_VENV_PYTHON" \
    "/Users/wsm/Project/ClaudeTeam/.venv/bin/python3" \
    "/srv/ai/ClaudeTeam/.venv/bin/python3" \
    "python3"; do
    if [ -n "$_candidate" ] && command -v "$_candidate" &>/dev/null; then
        _CT_VENV_PYTHON="$_candidate"
        break
    fi
done

if [ -z "$_CT_VENV_PYTHON" ]; then
    echo "api-cost-guard: no python3 found, skipping check" >&2
    exit 0
fi

# Read the command from stdin (hook protocol passes it this way).
COMMAND="$(cat)"

# Pipe to the Python cost guard module.
echo "$COMMAND" | "$_CT_VENV_PYTHON" -m claudeteam.runtime.api_cost_guard
exit $?
