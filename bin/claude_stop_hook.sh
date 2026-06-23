#!/usr/bin/env bash
# Run a hatch dev command only when the working tree has changes.
# Intended for use as a Claude Code Stop hook so checks are skipped on
# purely conversational turns where no files were modified.
#
# Usage: bin/claude_stop_hook.sh dev:test-hook
#        bin/claude_stop_hook.sh dev:type-check
#        bin/claude_stop_hook.sh dev:fix
#
# On failure, outputs {"decision":"block","reason":"<output>"} so Claude
# sees the full error and is blocked from finishing the turn.

set -euo pipefail

command="$1"
MAX_LINES=120

# Skip if background agents are still running. SubagentStart appends a line to
# `in`, SubagentStop appends to `out`, and SessionStart resets both. While more
# agents have started than have stopped (in > out), at least one is still
# working — defer so we never lint/test/type-check half-written files.
# NB: the counters are deliberately NOT cleared anywhere in this script. Clearing
# them mid-run orphaned the SubagentStop of still-in-flight agents and corrupted
# the count (in < out forever); SessionStart is the only place that resets them.
AGENTS_DIR=".claude/agents_running"
if [ -d "$AGENTS_DIR" ]; then
    in_count=$(wc -l < "$AGENTS_DIR/in" 2>/dev/null || echo 0)
    out_count=$(wc -l < "$AGENTS_DIR/out" 2>/dev/null || echo 0)
    if [ "$in_count" -gt "$out_count" ]; then
        jq -n --arg cmd "$command" --argjson in "$in_count" --argjson out "$out_count" \
            '{"decision":"approve","reason":"Skipped \($cmd): \($in - $out) background agent(s) still running."}'
        exit 0
    fi
fi

# Skip if no tracked files have been modified or staged.
# Untracked files are intentionally ignored — persistent local files (.env,
# uv.lock, etc.) would otherwise defeat the early-exit on every turn.
if git diff --quiet HEAD; then
    exit 0
fi

if output=$(hatch run "$command" 2>&1); then
    exit 0
fi

# Strip ANSI escape codes so the reason is plain text.
clean=$(printf '%s' "$output" | sed 's/\x1b\[[0-9;]*[mKHfABCDJsu]//g')

# Truncate to the last MAX_LINES lines to avoid flooding context.
# pytest prints its failure summary at the end, so tail preserves
# the most actionable information.
total_lines=$(printf '%s' "$clean" | wc -l | tr -d ' ')
if [ "$total_lines" -gt "$MAX_LINES" ]; then
    truncated=$(printf '%s' "$clean" | tail -n "$MAX_LINES")
    reason="[Output truncated: showing last $MAX_LINES of $total_lines lines]

$truncated"
else
    reason="$clean"
fi

jq -n --arg reason "$reason" '{"decision":"block","reason":$reason}'
