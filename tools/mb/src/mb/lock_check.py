from pathlib import Path

from . import console, runner


def run_check(repo_root: Path) -> int:
    """Fail if uv.lock is out of date with any workspace pyproject.toml.

    Mirrors the CI ``uv lock --check`` guard so a stale lock is caught at commit time
    instead of slipping into the branch (CI installs with ``uv sync --frozen``, which
    trusts the lock and never checks it against pyproject). ``uv lock --check`` is
    read-only — it never rewrites the lock — so it is safe to run inside a git hook.
    """
    exit_code, output = runner.run_quiet(["uv", "lock", "--check"], cwd=repo_root)
    if exit_code == 0:
        console.success("uv.lock is up to date.")
        return 0

    console.error("uv.lock is out of date with pyproject.toml.")
    if output.strip():
        console.raw(output.rstrip())
    console.raw("\nRefresh and stage the lockfile before committing:\n  uv sync\n  git add uv.lock")
    return exit_code
