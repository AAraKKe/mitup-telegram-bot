---
name: git
description: >
  Git workflow helper for the mitup-telegram-bot project. Use this skill whenever
  the user wants to: create a branch, stage files, commit changes, push a branch,
  or work with the GitLab remote. Also trigger when the user says things like
  "commit my changes", "push this", "make a commit", "create a branch", "stage
  these files", "get this ready for review", or any other git-related action.
  This project has specific commit format rules and dangerous-file guards — always
  consult this skill before touching git, even for simple commits.
user-invocable: true
argument-hint: "[branch|stage|commit|push] [optional args]"
allowed-tools: Bash, Read, AskUserQuestion
---

## Commit format (read this first)

This project uses conventional commits with emoji prefixes. The format depends on
whether pre-commit hooks are installed:

**Check:** `test -x .git/hooks/commit-msg && echo installed || echo not-installed`

- **Hooks installed** → write `Type[(scope)]: description` — the hook prepends the emoji automatically.
  Example: `Feat(auth): add JWT login`
- **Hooks NOT installed** → write `{emoji} description` — include the emoji directly.
  Example: `✨ Add JWT login`

Type/emoji mapping (also in `commits_check_config.yaml`):

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
| Merge | 🔀 | Merge branches |
| WIP | 🚧 | Work in progress (avoid on main) |
| Revert | ⏪ | Reverting a previous commit |

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
   - `repro_*.py`, `validate.po`, `.cursorignore`, `plan.md`, `report.json` — local scratch files
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
3. Check if pre-commit hooks are installed: `test -x .git/hooks/commit-msg && echo installed || echo not-installed`
4. Infer the commit type from the diff (refer to the table above).
5. Ask for the commit description if not provided via `$ARGUMENTS`.
6. Build the commit message based on hook status:
   - **Hooks installed:** `Type[(scope)]: description` (hook adds emoji automatically)
   - **Hooks not installed:** `{emoji} description`
7. **Deployment flags** — append to the title when the user asks to deploy or skip:
   - `[force-deploy]` — forces a staging deployment from `main` even when no deployment-related files changed.
   - `[no-deploy]` — suppresses deployment even when deployment files changed.
   - These are matched against `$CI_COMMIT_TITLE` (first line only), so they must be in the title.
   - Production deploys only from the `release` branch; `[force-deploy]` only affects staging.
8. Run `git commit -m "<message>"`.
9. Run `git status` to confirm the working tree is clean.

---

## Pushing (`/git push`)

1. Run `git branch --show-current` and `git status` to confirm the branch and that it's clean.
2. Check if the branch has an upstream: `git rev-parse --abbrev-ref @{u} 2>/dev/null`
   - **No upstream (first push):** `git push -u origin <branch>`
   - **Has upstream:** `git push`
3. **Never force-push to `main`.** For feature branches, `--force-with-lease` is acceptable after a rebase, but ask the user first.
4. After pushing, offer to run `/mr` to generate the MR description.

---

## Rebasing

Do not rebase on behalf of the user — ask them to do it instead. This avoids accidentally rewriting shared history.

When a rebase is needed (e.g., the feature branch has fallen behind `main` and there are conflicts to resolve), tell the user:

```bash
git fetch origin
git rebase origin/main
```

If they hit conflicts, guide them: resolve the conflicted files, then `git rebase --continue`. If they want to abort, `git rebase --abort`.
