from pathlib import Path

from . import console, runner


def run_check(repo_root: Path) -> int:
    """Fail if uv.lock is out of date with any workspace pyproject.toml.

    Runs both in the git hook and in CI so a stale lock is caught before it reaches a
    branch (CI installs with ``uv sync --frozen``, which trusts the lock and never checks
    it against pyproject). ``uv lock --check`` is read-only — it never rewrites the lock —
    so it is safe to run inside a git hook.

    ``UV_FROZEN`` is dropped from the child environment: the CI image bakes ``UV_FROZEN=1``
    so job-time ``uv sync``/``uv run`` reuse the pre-built venv, but for ``uv lock`` that
    same flag maps to ``--check-exists`` (does the lock file exist), which silently
    replaces the up-to-date comparison we want here — and conflicts with ``--check`` on
    newer uv. Unsetting it forces the real drift check regardless of ambient environment.
    """
    exit_code, output = runner.run_quiet(["uv", "lock", "--check"], cwd=repo_root, drop_env=["UV_FROZEN"])
    if exit_code == 0:
        console.success("uv.lock is up to date.")
        return 0

    console.error("uv.lock is out of date with pyproject.toml.")
    if output.strip():
        console.raw(output.rstrip())
    console.raw("\nRefresh and stage the lockfile before committing:\n  uv sync\n  git add uv.lock")
    return exit_code
