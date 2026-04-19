---
name: worktree-setup
description: Bootstrap a fresh git worktree so its environment matches the main checkout. The skill copies the uncommitted `.env` (and `.envrc` if present) from the main checkout — these files aren't tracked by git, so `git worktree add` doesn't bring them along, and without them `hatch run dev:*` commands pick up the wrong config. It also tells you what else is needed (dependencies, migrations, a running database) without taking those actions itself — so it can't hang on an unavailable Docker daemon or a missing Postgres. Invoke whenever a fresh worktree was just created (manually with `git worktree add` or automatically by `em`'s `EnterWorktree`), or when someone reports "the bot won't start in this worktree".
user-invocable: true
argument-hint: "[no args needed — operates on the current worktree]"
allowed-tools: Bash, Read
---

# Worktree Setup

`git worktree add` creates a parallel checkout that shares history with the main clone but gets its own working directory. What it does **not** copy is any file that git doesn't track — and this project's local-only configuration (`.env`, optionally `.envrc`) lives outside of git on purpose. Without those files, `hatch run dev:*` commands either pick up the wrong environment or fail outright.

This skill closes that specific gap. It only performs **safe, fast, offline** actions — nothing that needs an external service running. Anything that requires Postgres, Docker, or a package install is documented below but left for the user to run explicitly, when they have the tooling up.

## What the skill does automatically

1. **Locate the main worktree.** From inside any worktree, the main checkout is the parent directory of `$(git rev-parse --git-common-dir)` — `git-common-dir` points at the shared `.git` database, which for a worktree is the main clone's `.git`. (In the main checkout itself this path is just `.git`, and the main *is* the main — nothing to copy.)

2. **Copy the local-only files.** For each file below, if it exists in the main worktree but **not** in the current worktree, copy it over. Never overwrite a file that already exists — the user may have diverged it intentionally.
   - `.env` — local environment variables (always copy when present in main)
   - `.envrc` — direnv local config (copy if present)

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

for f in .env .envrc; do
    if [ -f "$MAIN/$f" ] && [ ! -f "./$f" ]; then
        cp "$MAIN/$f" "./$f"
        echo "copied $f from main"
    fi
done
```

After it finishes, print a reminder of the non-automatic steps (below) so the user knows what still needs to happen before the bot can run.

## What the skill does **not** do, and how to do it

These steps need external services or durable state — running them automatically from the skill creates a failure surface the skill can't recover from (Docker not running, Postgres not started, hatch env not yet built). The skill reports them as next steps and stops.

- **Install / build the hatch env.** Hatch creates its env on first use. Force it eagerly with:

  ```bash
  hatch env create dev
  ```

- **Start the local database.** Typically a Docker container — refer to the project README for the exact command. The database host/credentials come from the just-copied `.env`.

- **Apply pending migrations.** Once Postgres is reachable:

  ```bash
  hatch run dev:migrations-upgrade
  ```

- **Fetch / refresh secrets.** `.env` is copied verbatim from the main checkout. If those secrets are stale, refresh them in the main checkout first, then re-run this skill so the update flows into the worktree.

## When `em` invokes this skill

`em`'s Step 6 runs this skill immediately after `EnterWorktree`. Because the skill only copies files and never waits on a service, that invocation is safe even when Docker or the local database is offline — the user can bring the database up whenever they're ready and run the migration command themselves.

If you're writing a new workflow that creates worktrees, do the same: invoke this skill right after the worktree is created, so the env files are in place, and surface the "next steps" list to the user without blocking on them.
