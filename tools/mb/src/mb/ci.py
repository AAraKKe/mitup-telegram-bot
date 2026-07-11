from pathlib import Path
from typing import Annotated

import typer

from . import commit_check, language_matrix, lock_check, runner, ty_ignores

app = typer.Typer(no_args_is_help=True, help="CI checks (normally run by the pipeline).")


@app.command("check-commit")
def check_commit(
    commit_msg_file: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, help="Path to the commit message file.")
    ],
):
    """Validate commit message format and replace the type prefix with its emoji."""
    config_path = runner.repo_root() / "commits_check_config.yaml"
    raise typer.Exit(commit_check.check_commit_file(commit_msg_file, config_path))


@app.command("check-lock")
def check_lock():
    """Fail if uv.lock is out of date with any workspace pyproject.toml."""
    raise typer.Exit(lock_check.run_check(runner.repo_root()))


@app.command("check-ty-ignores")
def check_ty_ignores():
    """Validate that every ty suppression carries a GitHub issue URL, and flag closed issues."""
    raise typer.Exit(ty_ignores.run_check(runner.repo_root()))


@app.command("check-languages")
def check_languages():
    """Validate the CI language matrix against the supported languages."""
    raise typer.Exit(language_matrix.run_check(runner.repo_root()))
