import os
import shlex
import subprocess
from collections.abc import Sequence
from pathlib import Path

from . import console


def repo_root() -> Path:
    """Workspace root: the ancestor of this file that holds `uv.lock`.

    Resolved without git so mb works where the binary is absent (containers) or where
    `.git` is a worktree pointer to a path that only exists on the host.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "uv.lock").is_file():
            return candidate
    raise RuntimeError(f"No uv.lock found in any parent of {Path(__file__).resolve()}.")


def build_env(
    extra_env: dict[str, str] | None,
    *,
    drop_env: Sequence[str] | None = None,
) -> dict[str, str] | None:
    """Merge *extra_env* over the current environment, removing every name in *drop_env*.

    Returns None (inherit the environment unchanged) when neither is given.
    """
    if not extra_env and not drop_env:
        return None
    env: dict[str, str] = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    for name in drop_env or ():
        env.pop(name, None)
    return env


def location_note(target: Path, root: Path) -> str | None:
    """Path of *target* relative to *root* for the command echo, or None when it is the root."""
    if target == root:
        return None
    try:
        return str(target.relative_to(root))
    except ValueError:
        return str(target)


def run_command(args: list[str], *, cwd: Path | None = None, extra_env: dict[str, str] | None = None) -> int:
    """Run *args* from the repo root (or *cwd*), streaming its output, and return its exit code."""
    root = repo_root()
    target = cwd or root
    console.command_echo(shlex.join(args), location=location_note(target, root))
    completed = subprocess.run(args, cwd=target, env=build_env(extra_env))
    return completed.returncode


def run_quiet(
    args: list[str],
    *,
    cwd: Path | None = None,
    extra_env: dict[str, str] | None = None,
    drop_env: Sequence[str] | None = None,
) -> tuple[int, str]:
    """Run *args* capturing combined stdout+stderr, for short steps wrapped in a spinner."""
    completed = subprocess.run(
        args,
        cwd=cwd or repo_root(),
        env=build_env(extra_env, drop_env=drop_env),
        capture_output=True,
        text=True,
    )
    return completed.returncode, completed.stdout + completed.stderr


def run_step(description: str, args: list[str], *, cwd: Path | None = None) -> int:
    """Run a quiet, short command behind a spinner, dumping its captured output only on failure."""
    root = repo_root()
    target = cwd or root
    console.command_echo(shlex.join(args), location=location_note(target, root))
    with console.spinner(description):
        exit_code, captured = run_quiet(args, cwd=target)
    if exit_code == 0:
        console.success(description)
        return exit_code
    if captured.strip():
        console.raw(captured.rstrip())
    console.error(f"{description} failed (exit code {exit_code}).")
    return exit_code


def uv(*args: str, cwd: Path | None = None, extra_env: dict[str, str] | None = None) -> int:
    """Run `uv run <args>` streaming its output, and return its exit code."""
    return run_command(uv_argv(*args), cwd=cwd, extra_env=extra_env)


def uv_argv(*args: str) -> list[str]:
    """Build the `uv run ...` argv without executing, for callers that assemble commands."""
    return ["uv", "run", *args]
