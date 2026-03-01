---
name: git
description: Git workflow helper for this project. Covers branching, staging, committing with emoji prefixes, pushing, and rebasing onto main.
user-invocable: true
argument-hint: "[branch|stage|commit|push|rebase] [optional args]"
allowed-tools: Bash, Read, AskUserQuestion
---

Read `commits_check_config.yaml` for the type→emoji mapping before committing.
Always check if pre-commit hooks are installed before committing.
   1. If installed, use `type: description` for the commit message.
   2. If not installed, use the emoji directly.

---

## Branching (`/git branch`)

Always branch from `main` (fetch first to avoid stale base):

```bash
git fetch origin
git checkout -b NN-short-description origin/main
```

**Branch naming:** `NN-short-description` — issue number prefix (if working on an issue), lowercase kebab-case, concise.
Examples: `145-add-past-meetings-list`, `fix-timezone-offset`, `migrate-agents-to-claude-code`

**Safety:** If the user is already on a feature branch with uncommitted changes, warn before switching.
**Never create a branch from a dirty working tree** — commit or stash first and ask the user before taking this action.

---

## Staging (`/git stage`)

Review before staging — never blindly `git add .`:

1. Run `git status` to see all changed and untracked files.
2. Flag files that should **not** be committed:
   - `.env` — local secrets, never commit
   - `uv.lock` — only commit if explicitly updating dependencies
   - `repro_*.py`, `validate.po`, `.cursorignore` — local scratch files
3. For each relevant changed file, run `git diff <file>` to review.
4. Stage specific files: `git add <file1> <file2> ...`
5. Run `git diff --staged --stat` to confirm what will be committed.

---

## Committing (`/git commit`)

1. Run `git branch --show-current` to check the current branch.
   - **If on `main`**: ask whether to create a feature branch first or commit directly.
     - Feature branch: ask for issue number and short name, suggest `NN-short-description`, run `git checkout -b <branch> origin/main`.
     - Direct to main: require explicit confirmation ("yes, commit to main"). Do not proceed until confirmed.
2. If nothing is staged, offer to run the staging workflow above.
3. Infer the commit type from the diff:

   | Type | Emoji | When |
   |------|-------|------|
   | Feat | ✨ | New feature |
   | Fix | 🐛 | Bug fix |
   | Docs | 📚 | Documentation only |
   | Refactor | 🧹 | Code restructure, no behaviour change |
   | Test | 🧪 | Tests added or updated |
   | CI | 👷 | CI/CD pipeline changes |
   | Chore | ⚙️ | Config, tooling, housekeeping |
   | Infra | 🏗️ | Infrastructure changes |
   | Translations | 🗣️ | Locale/translation updates |
   | Monitoring | 📈 | Metrics/observability |
   | Update | ⬆️ | Dependency updates |
   | WIP | 🚧 | Work in progress (avoid on main) |
   | Revert | ⏪ | Reverting a previous commit |

4. Ask for the commit description if not provided via `$ARGUMENTS`.
5. Build the message: `{emoji} {description}` — emoji only, no conventional type prefix.
6. Run `git commit -m "{emoji} {description}"`.
7. Run `git status` to confirm the working tree is clean.

**Pre-commit hooks note:** Hooks are installed locally and auto-convert `Type: description` → emoji. When hooks are absent (CI agents), use the emoji directly.

---

## Pushing (`/git push`)

1. Run `git branch --show-current` and `git status` to confirm the branch and that it's clean.
2. Check if the branch has an upstream: `git rev-parse --abbrev-ref @{u} 2>/dev/null`
   - **No upstream (first push):** `git push -u origin <branch>`
   - **Has upstream:** `git push`
3. **Never force-push to `main`.** For feature branches, `--force-with-lease` is acceptable after a rebase, but ask first.
4. After pushing, offer to run `/mr` to generate the MR description.

---

## Rebasing (`/git rebase`)

Never rebase. If needed ask the user to do it.
