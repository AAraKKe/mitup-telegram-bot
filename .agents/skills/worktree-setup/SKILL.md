---
name: worktree-setup
description: Bootstrap a fresh git worktree so it is ready to build and commit — copies the untracked local config (`.env`, `.envrc`, dev.toml) from the main checkout and syncs the venv, then lists the service-dependent steps (database, migrations) without running them. Invoke right after creating a worktree (manually or from a workflow), when a first `git commit` there fails with `Failed to spawn: mb`, or when someone reports "the bot won't start in this worktree".
user-invocable: true
argument-hint: "[no args needed — operates on the current worktree]"
allowed-tools: Bash, Read
---

# Worktree Setup

`git worktree add` creates a parallel checkout that shares history with the main clone but gets its own working directory. What it does **not** copy is any file that git doesn't track — and this project's local-only configuration (`.env`, optionally `.envrc`) lives outside of git on purpose. Without those files, `uv run` commands either pick up the wrong environment or fail outright.

This skill closes that specific gap. It only performs **safe actions that need no running service** — nothing that depends on Docker or Postgres. Anything that does is documented below but left for the user to run explicitly, when they have the tooling up.

## What the skill does automatically

1. **Locate the main worktree.** From inside any worktree, the main checkout is the parent directory of `$(git rev-parse --git-common-dir)` — `git-common-dir` points at the shared `.git` database, which for a worktree is the main clone's `.git`. (In the main checkout itself this path is just `.git`, and the main *is* the main — nothing to copy.)

2. **Copy the local-only files.** For each file below, if it exists in the main worktree but **not** in the current worktree, copy it over. Never overwrite a file that already exists — the user may have diverged it intentionally.
   - `.env` — local environment variables (always copy when present in main)
   - `.envrc` — direnv local config (copy if present)
   - `libs/core/mitup_bot/environments/dev.toml` — the local dev-bot configuration written by `mb setup --bot-token` (copy if present; without it `mb run bot` has no dev environment). The parent directory is tracked, so a plain `cp` works.

3. **Sync the dependencies.** Run `uv sync` in the worktree. Each worktree gets its own `.venv`, and the git hooks invoke `uv run --no-sync --frozen mb ...` — with no venv, the very first `git commit` fails with `Failed to spawn: mb`. `uv sync` resolves nothing (it installs from `uv.lock`) and is near-instant when the uv cache is warm.

### Exact command sequence

Run from the worktree that needs bootstrapping:

```bash
# Normalize both paths to absolute — `git rev-parse --git-common-dir` returns
# a relative `.git` in the main checkout, which would miscompare against the
# absolute path from `--show-toplevel`.
MAIN="$(cd "$(dirname "$(git rev-parse --git-common-dir)")" && pwd)"
CURRENT="$(git rev-parse --show-toplevel)"

if [ "$CURRENT" = "$MAIN" ]; then
    echo "Already in the main worktree — nothing to do."
    exit 0
fi

for f in .env .envrc libs/core/mitup_bot/environments/dev.toml; do
    if [ -f "$MAIN/$f" ] && [ ! -f "./$f" ]; then
        cp "$MAIN/$f" "./$f"
        echo "copied $f from main"
    fi
done

uv sync
```

After it finishes, print a reminder of the non-automatic steps (below) so the user knows what still needs to happen before the bot can run.

## What the skill does **not** do, and how to do it

These steps need external services — running them automatically from the skill creates a failure surface the skill can't recover from (Docker not running, Postgres not started). The skill reports them as next steps and stops.

- **Start the local database.** Run `uv run mb db up` to start the Postgres container and wait for it to be healthy. Credentials are hardcoded in `docker-compose.yaml` for the container, and in the just-copied `dev.toml` for host runs.

- **Apply pending migrations.** Once Postgres is reachable:

  ```bash
  uv run mb db migrate up
  ```

- **Fetch / refresh secrets.** `.env` is copied verbatim from the main checkout. If those secrets are stale, refresh them in the main checkout first, then re-run this skill so the update flows into the worktree.

## When a workflow invokes this skill

Because the skill only copies files and syncs the venv from the lock, never waiting on a service, invoking it from an automated workflow is safe even when Docker or the local database is offline — the user can bring the database up whenever they're ready and run the migration command themselves.

If you're writing a workflow that creates worktrees, invoke this skill right after the worktree is created, so the env files are in place, and surface the "next steps" list to the user without blocking on them.
