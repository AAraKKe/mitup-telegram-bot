import os
import shlex
import subprocess
from pathlib import Path

from rich.console import Console

console = Console()


def repo_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(result.stdout.strip())


def run_command(args: list[str], *, cwd: Path | None = None, extra_env: dict[str, str] | None = None) -> int:
    """Run *args* from the repo root (or *cwd*) and return its exit code."""
    env = {**os.environ, **extra_env} if extra_env else None
    console.print(f"[dim]$ {shlex.join(args)}[/dim]")
    completed = subprocess.run(args, cwd=cwd or repo_root(), env=env)
    return completed.returncode


def uv(*args: str) -> list[str]:
    return ["uv", "run", *args]
