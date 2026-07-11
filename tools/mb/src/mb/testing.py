from pathlib import PurePosixPath
from typing import Annotated

import typer

from . import console, locales, runner

DEFAULT_ARGS = ["tests"]
DEFAULT_DB_ARGS = ["tests/data/db_behavior/"]
DEFAULT_COV_TARGETS = ["mitup_bot"]

FAST_ARGS = ["-n", "4", "--no-cov", "--tb=short", "-q"]
COV_REPORT_ARGS = ["--cov-report", "term-missing:skip-covered"]
DB_ARGS = ["--db-tests", "--dist", "no"]

# The members whose tests render user-facing text; their language-marked tests explode over the
# locale matrix in CI, while every other member runs once.
I18N_MEMBERS = ["apps/bot", "libs/telegram", "apps/events"]

# The dev CLI is the one member whose source is the `mb` package under `src/` rather than a
# slice of the `mitup_bot` namespace, so its coverage target is a path instead of the package.
MB_MEMBER = "tools/mb"


def member_test_path(member: str) -> str:
    """Derive a member's test subtree from its workspace path (`apps/bot` -> `tests/bot`).

    `tests/` mirrors the member layout, so the subtree is the member's leaf name under `tests/`. A
    member with no tests resolves to a missing directory, which fails the run loudly rather than
    silently passing.
    """
    return str(PurePosixPath("tests") / PurePosixPath(member).name)


def member_cov_target(member: str) -> str:
    """The coverage source a member's run measures.

    Every member that ships `mitup_bot` code measures the whole namespace, not just its own slice:
    a member's tests exercise shared libraries too, and CI unions the per-entry coverage data into
    one report, so measuring the full namespace is what lets that union reach the real total. The
    dev CLI is the exception — its code is the `mb` package, not `mitup_bot`.
    """
    if member == MB_MEMBER:
        return "tools/mb/src/mb"
    return "mitup_bot"


def cov_flags(cov_targets: list[str]) -> list[str]:
    """The coverage flags for *cov_targets* (one ``--cov`` per source path/package)."""
    return [*(f"--cov={target}" for target in cov_targets), *COV_REPORT_ARGS]


def resolve_members(members: list[str], *, i18n: bool) -> list[str]:
    """Combine explicitly requested *members* with the language-matrix set when ``--i18n`` is on."""
    selected = [*members, *(I18N_MEMBERS if i18n else [])]
    return list(dict.fromkeys(selected))


def default_target(members: list[str], *, db: bool) -> list[str]:
    """The test paths a bare invocation runs: the db suite, the selected members, or the whole tree."""
    if db:
        return DEFAULT_DB_ARGS
    if members:
        return [member_test_path(member) for member in members]
    return DEFAULT_ARGS


def build_pytest_command(
    user_args: list[str],
    extra_flags: list[str] | None = None,
    *,
    cov: bool = False,
    db: bool = False,
    lang: str | None = None,
    members: list[str] | None = None,
    i18n: bool = False,
) -> list[str]:
    """Build the `uv run pytest ...` invocation.

    *user_args* replace the default target entirely when present (paths or bare pytest flags); when
    empty the target falls back to the db suite, the ``--member``/``--i18n`` subtrees, or the whole
    tree. *extra_flags* (the ``-m``/``--junit``/``--cov-xml`` options) are always appended, so they
    never displace the resolved target the way a stray positional flag would.
    """
    selected_members = resolve_members(members or [], i18n=i18n)
    targets = list(dict.fromkeys(member_cov_target(member) for member in selected_members)) or DEFAULT_COV_TARGETS

    match (db, cov):
        # The db suite runs serially (shared Postgres), so it drops the xdist `-n 4` when it also
        # measures coverage; CI unions its data file into the combined report with the rest.
        case (True, True):
            mode_args = [*DB_ARGS, *cov_flags(targets)]
        case (True, False):
            mode_args = DB_ARGS
        case (False, True):
            mode_args = ["-n", "4", *cov_flags(targets)]
        case (False, False):
            mode_args = FAST_ARGS

    command = runner.uv_argv("pytest", *mode_args)
    if lang:
        command.extend(["--lang", lang])
    command.extend(user_args or default_target(selected_members, db=db))
    command.extend(extra_flags or [])
    return command


def run_tests(
    user_args: list[str],
    extra_flags: list[str] | None = None,
    *,
    cov: bool = False,
    db: bool = False,
    lang: str | None = None,
    members: list[str] | None = None,
    i18n: bool = False,
) -> int:
    command = build_pytest_command(user_args, extra_flags, cov=cov, db=db, lang=lang, members=members, i18n=i18n)
    build_exit = locales.ensure_locales_built()
    if build_exit != 0:
        return build_exit
    # Long cov/db runs keep color when piped (CI logs render it) — unless the full
    # kill-switch is on, in which case the subprocess must stay ANSI-free too.
    extra_env = {"FORCE_COLOR": "1"} if (cov or db) and not console.plain_active else None
    return runner.run_command(command, extra_env=extra_env)


def report_flags(marker: str | None, junit: str | None, cov_xml: str | None) -> list[str]:
    """Turn the typed reporting options into pytest flags, kept off the positional target."""
    flags: list[str] = []
    if marker:
        flags.extend(["-m", marker])
    if junit:
        flags.append(f"--junitxml={junit}")
    if cov_xml:
        flags.extend(["--cov-report", f"xml:{cov_xml}"])
    return flags


def test_command(
    ctx: typer.Context,
    paths: Annotated[list[str] | None, typer.Argument(help="Test paths and/or pytest args (default: tests/).")] = None,
    cov: Annotated[bool, typer.Option("--cov", help="Run with coverage reporting (slower).")] = False,
    db: Annotated[bool, typer.Option("--db", help="Run the Postgres-backed db suite.")] = False,
    lang: Annotated[str | None, typer.Option("--lang", help="Language(s) to test, or 'all'.")] = None,
    member: Annotated[
        list[str] | None,
        typer.Option("--member", help="Restrict to a workspace member's tests (repeatable), e.g. libs/data."),
    ] = None,
    i18n: Annotated[
        bool, typer.Option("--i18n", help="Target the language-rendering members (the matrix set).")
    ] = False,
    marker: Annotated[
        str | None, typer.Option("-m", "--marker", help="Pytest marker expression, e.g. 'not i18n'.")
    ] = None,
    junit: Annotated[str | None, typer.Option("--junit", help="Write a JUnit XML report to this path.")] = None,
    cov_xml: Annotated[
        str | None, typer.Option("--cov-xml", help="Write a Cobertura coverage XML report to this path.")
    ] = None,
):
    """Run the test suite (fast mode by default; extra args pass through to pytest)."""
    user_args = [*(paths or []), *ctx.args]
    extra_flags = report_flags(marker, junit, cov_xml)
    exit_code = run_tests(user_args, extra_flags, cov=cov, db=db, lang=lang, members=member or [], i18n=i18n)
    raise typer.Exit(exit_code)
