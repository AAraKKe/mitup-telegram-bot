---
name: cleanup-branches
description: >-
  Clean up stale local git branches whose GitLab merge request has already been merged — interactively and safely. Handles three cases: (1) the branch is in sync with its upstream and the MR is merged → delete without asking; (2) the branch has local commits that aren't on the remote but the remote MR is still merged → surface each one individually and ask the user to keep / rebase / delete; (3) the branch has no upstream or no MR → skip and report. Use this skill whenever the user asks to "clean up branches", "delete old branches", "prune merged branches", "tidy the repo", or reports that they have too many stale branches lying around. It never force-deletes, never touches `main`/`release`/the currently-checked-out branch, and never pushes anything.
user-invocable: true
argument-hint: "[no args needed — operates on the current repo]"
allowed-tools: Bash, AskUserQuestion
---

# Cleanup Branches

Local branches accumulate: you merge an MR on GitLab, the remote branch is deleted, the upstream-tracking link dies, but the local branch sticks around forever. This skill finds those and removes them — cautiously.

## Principles

1. **Never delete work that might not be safely merged.** If the local branch has commits that aren't on `origin/main`, and the associated MR isn't "merged" on GitLab, *stop* and ask — the commits may be uncommitted progress, not merged work.
2. **Never touch protected branches.** `main`, `release`, and the branch that is currently checked out are always skipped.
3. **No `git push`.** This skill is for local hygiene. Deleting remote branches is out of scope — GitLab's "delete source branch on merge" handles that.
4. **No force-delete without confirmation.** Default to `git branch -d` (refuses to delete unmerged work). Only use `git branch -D` after the user has explicitly said "delete it" for that specific branch.

## Workflow

### Step 1 — Refresh and enumerate

```bash
git fetch --prune origin
```

`--prune` removes stale `origin/<branch>` references for branches that have been deleted on the remote (typical post-merge state).

List local branches, skipping protected ones and the current checkout:

```bash
git branch --format='%(refname:short) %(upstream:short) %(upstream:track)'
```

The output gives you each branch's upstream tracking state (`[gone]` means the remote is deleted; `ahead N` / `behind N` describe divergence).

### Step 2 — Classify each branch

For each branch, determine which bucket it falls into. If `glab` is available, use it to resolve the merge status authoritatively; otherwise fall back to what git alone can tell you.

```bash
glab mr list --source-branch "<branch>" --merged --output json
```

An empty result plus `upstream: gone` usually means the MR was merged and the remote branch deleted.

| Bucket | Signals | Action |
|---|---|---|
| **Safely merged and in sync** | Upstream gone (remote branch deleted) **AND** no unmerged commits (`git branch -d` would succeed) **AND** an MR from this branch was merged on GitLab. | Delete with `git branch -d <branch>`. Report in a summary, don't ask per-branch. |
| **Diverged but merged upstream** | Upstream gone **AND** `git branch -d` refuses (unmerged commits from git's local perspective) **AND** an MR from this branch *was* merged on GitLab. | Surface to the user. The merged MR content likely arrived via squash, so git's "unmerged" check is overconservative — but the user should still confirm. |
| **Not merged** | No merged MR found (open MR, closed-not-merged MR, or no MR at all). | Skip. Tell the user it was skipped and why. |
| **Current branch / protected** | `main`, `release`, or currently checked out. | Skip silently. |

### Step 3 — Delete the safe ones, ask about the rest

First, delete the whole "Safely merged and in sync" bucket in one pass and print a summary:

```bash
git branch -d <branch-1> <branch-2> ...
```

Then, for each "Diverged but merged upstream" branch, use `AskUserQuestion` (one question per branch) with options:

- **Delete** — run `git branch -D <branch>` (force, because `-d` refused). Only do this if the user picks it explicitly.
- **Rebase onto `main`** — tell the user the command (`git rebase origin/main <branch>` from a clean working tree) and let them decide when to run it; do **not** run rebases on behalf of the user.
- **Keep** — leave the branch alone.

Never offer a "delete all" shortcut for this bucket — each branch gets its own explicit confirmation.

### Step 4 — Summary report

At the end, print a concise summary:

- Branches deleted (safe bucket).
- Branches kept / rebased / deleted (diverged bucket, per user choice).
- Branches skipped and the reason (not merged, protected, current).

## What this skill does **not** do

- **No remote branch deletion.** This is local-only.
- **No rebasing on the user's behalf.** When a rebase is the right answer the skill explains it and steps aside — this matches the `git` skill's stance on rebasing.
- **No rewriting of any branch's history.** `-D` is the most destructive operation it runs, and only with explicit per-branch consent.
- **No worktree cleanup.** If a worktree has a branch checked out, the branch can't be deleted anyway; point the user at `git worktree remove` separately.
