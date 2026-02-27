#!/usr/bin/env bash
# Run a hatch dev command only when the working tree has changes.
# Intended for use as a Claude Code Stop hook so checks are skipped on
# purely conversational turns where no files were modified.
#
# Usage: bin/claude_stop_hook.sh dev:test
#        bin/claude_stop_hook.sh dev:type-check
#
# On failure, outputs {"decision":"block","reason":"<output>"} so Claude
# sees the full error and is blocked from finishing the turn.

set -euo pipefail

command="$1"

# Skip if the working tree is clean (no modified, staged, or new files).
if git diff --quiet HEAD && [ -z "$(git ls-files --others --exclude-standard)" ]; then
    exit 0
fi

output=$(hatch run "$command" 2>&1)
code=$?

[ $code -eq 0 ] || jq -n --arg reason "$output" '{"decision":"block","reason":$reason}'
