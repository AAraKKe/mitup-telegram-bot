#!/bin/sh
# Writes the changelog fragment of a Renovate branch from the data file that `postUpgradeTasks` in
# renovate.json renders: the branch name, the summary sentence, then one updated dependency per line.
set -eu

data="$RENOVATE_POST_UPGRADE_COMMAND_DATA_FILE"
slug=$(head -n 1 "$data" | tr -c 'A-Za-z0-9\n' '-')
summary=$(sed -n 2p "$data")
updates=$(tail -n +3 "$data" | sed '/^$/d' | sort -u | paste -s -d , - | sed 's/,/, /g')
if [ -n "$updates" ]; then
  summary="$summary $updates."
fi
note='<!-- This file has no merge request number in its name because the tool that wrote it ran before the merge request existed. `mb release prepare` renames it to <MR number>.internal.md and removes this comment. -->'
printf '%s\n%s\n' "$note" "$summary" > "changelog.d/+$slug-$(date -u +%Y%m%d%H%M%S).internal.md"
