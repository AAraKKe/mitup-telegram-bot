from typing import Annotated

import typer

from . import console, locales, runner

DEFAULT_ARGS = ["tests"]
DEFAULT_DB_ARGS = ["tests/models/db_behavior/"]

FAST_ARGS = ["-n", "4", "--no-cov", "--tb=short", "-q"]
COV_ARGS = ["-n", "4", "--cov=mitup_bot", "--cov-report", "term-missing:skip-covered"]
DB_ARGS = ["--db-tests", "--dist", "no"]


def build_pytest_command(
    user_args: list[str],
    *,
    cov: bool = False,
    db: bool = False,
    lang: str | None = None,
) -> list[str]:
    """Build the `uv run pytest ...` invocation.

    Any user-supplied args (paths or pytest flags) replace the default target entirely;
    pytest then falls back to its configured testpaths for bare-flag invocations.
    """
    match (db, cov):
        case (True, True):
            raise ValueError("--cov and --db are mutually exclusive: the db suite runs without coverage.")
        case (True, False):
            mode_args, default_args = DB_ARGS, DEFAULT_DB_ARGS
        case (False, True):
            mode_args, default_args = COV_ARGS, DEFAULT_ARGS
        case (False, False):
            mode_args, default_args = FAST_ARGS, DEFAULT_ARGS
    command = runner.uv_argv("pytest", *mode_args)
    if lang:
        command.extend(["--lang", lang])
    command.extend(user_args or default_args)
    return command


def run_tests(user_args: list[str], *, cov: bool = False, db: bool = False, lang: str | None = None) -> int:
    command = build_pytest_command(user_args, cov=cov, db=db, lang=lang)
    build_exit = locales.ensure_locales_built()
    if build_exit != 0:
        return build_exit
    # Long cov/db runs keep color when piped (CI logs render it) — unless the full
    # kill-switch is on, in which case the subprocess must stay ANSI-free too.
    extra_env = {"FORCE_COLOR": "1"} if (cov or db) and not console.plain_active else None
    return runner.run_command(command, extra_env=extra_env)


def test_command(
    ctx: typer.Context,
    paths: Annotated[list[str] | None, typer.Argument(help="Test paths and/or pytest args (default: tests/).")] = None,
    cov: Annotated[bool, typer.Option("--cov", help="Run with coverage reporting (slower).")] = False,
    db: Annotated[bool, typer.Option("--db", help="Run the Postgres-backed db suite.")] = False,
    lang: Annotated[str | None, typer.Option("--lang", help="Language(s) to test, or 'all'.")] = None,
):
    """Run the test suite (fast mode by default; extra args pass through to pytest)."""
    user_args = [*(paths or []), *ctx.args]
    try:
        exit_code = run_tests(user_args, cov=cov, db=db, lang=lang)
    except ValueError as error:
        console.error(str(error))
        raise typer.Exit(2) from error
    raise typer.Exit(exit_code)
