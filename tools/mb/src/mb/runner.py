import os
import shlex
import subprocess
from pathlib import Path

from . import console


def repo_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(result.stdout.strip())


def build_env(extra_env: dict[str, str] | None) -> dict[str, str] | None:
    return {**os.environ, **extra_env} if extra_env else None


def run_command(args: list[str], *, cwd: Path | None = None, extra_env: dict[str, str] | None = None) -> int:
    """Run *args* from the repo root (or *cwd*), streaming its output, and return its exit code."""
    console.command_echo(shlex.join(args))
    completed = subprocess.run(args, cwd=cwd or repo_root(), env=build_env(extra_env))
    return completed.returncode


def run_quiet(args: list[str], *, cwd: Path | None = None, extra_env: dict[str, str] | None = None) -> tuple[int, str]:
    """Run *args* capturing combined stdout+stderr, for short steps wrapped in a spinner."""
    completed = subprocess.run(
        args,
        cwd=cwd or repo_root(),
        env=build_env(extra_env),
        capture_output=True,
        text=True,
    )
    return completed.returncode, completed.stdout + completed.stderr


def run_step(description: str, args: list[str], *, cwd: Path | None = None) -> int:
    """Run a quiet, short command behind a spinner, dumping its captured output only on failure."""
    console.command_echo(shlex.join(args))
    with console.spinner(description):
        exit_code, captured = run_quiet(args, cwd=cwd)
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
