import shutil
import subprocess
from pathlib import Path
from typing import Annotated

import typer

from . import runner, vscode

LOCAL_ONLY_FILES = (".env", ".envrc")


def main_checkout_root() -> Path:
    """Return the main checkout's root; in a worktree, `--git-common-dir` points into the main clone."""
    result = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=True,
        cwd=runner.repo_root(),
    )
    return Path(result.stdout.strip()).resolve().parent


def copy_local_only_files(main_root: Path, current_root: Path) -> list[str]:
    """Copy untracked local config from the main checkout, never overwriting existing files."""
    if main_root == current_root:
        return []
    copied: list[str] = []
    for name in LOCAL_ONLY_FILES:
        source = main_root / name
        target = current_root / name
        if source.is_file() and not target.exists():
            shutil.copy(source, target)
            copied.append(name)
    return copied


def setup_command(
    setup_vscode: Annotated[
        bool, typer.Option("--vscode", help="Also generate the VS Code workspace settings.")
    ] = False,
):
    """Bootstrap this checkout: local config files, dependencies, and git hooks. Idempotent."""
    current_root = runner.repo_root()
    for name in copy_local_only_files(main_checkout_root(), current_root):
        runner.console.print(f"Copied {name} from the main checkout.")
    sync_exit = runner.run_command(["uv", "sync"])
    if sync_exit != 0:
        raise typer.Exit(sync_exit)
    if shutil.which("pre-commit"):
        hooks_exit = runner.run_command(["pre-commit", "install"])
        if hooks_exit != 0:
            raise typer.Exit(hooks_exit)
    else:
        runner.console.print("[yellow]pre-commit not found — skipping git hook installation.[/yellow]")
    if setup_vscode:
        raise typer.Exit(vscode.apply_vscode_settings())
