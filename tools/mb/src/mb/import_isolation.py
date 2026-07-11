from dataclasses import dataclass, field
from pathlib import Path

from . import console, runner

# Representative imports per member: importing these exercises the member's whole runtime import
# graph, so success proves the member's declared dependencies are a sufficient closure. Each member
# is resolved in an ephemeral environment built ONLY from its own path plus the paths of its
# workspace dependencies (its PyPI dependencies come from its own pyproject). An accidental reliance
# on an undeclared dependency surfaces as an ImportError instead of passing silently in the shared
# workspace venv.


@dataclass(frozen=True)
class Member:
    name: str
    # Path of this member and of every workspace member it depends on, relative to the repo root.
    # The workspace edges have no resolver here (there is no workspace), so they are supplied by path.
    paths: tuple[str, ...]
    imports: str
    # Modules that MUST NOT be importable in this member's environment (a negative proof).
    forbidden_imports: tuple[str, ...] = field(default_factory=tuple)


CORE = "libs/core"
MONITORING = "libs/monitoring"
DATA = "libs/data"
TELEGRAM = "libs/telegram"
PATREON = "libs/patreon"

MEMBERS = (
    Member("mitup-core", (CORE,), "import mitup_bot.config; import mitup_bot.supporter; import mitup_bot.translations"),
    Member("mitup-monitoring", (MONITORING, CORE), "import mitup_bot.monitoring"),
    # python-telegram-bot is deliberately absent from the data environment: importing the models and
    # the engine proves the persistence layer is PTB-free, which is what lets the migrations Lambda
    # drop the PTB dependency.
    Member("mitup-data", (DATA, CORE, MONITORING), "import mitup_bot.models; import mitup_bot.db"),
    # The telegram layer ships the api wrapper, the views, the message utilities and the shared glue
    # (guards, custom_context, mitup_types, reconcile, api_guards) that both the bot and the events
    # app register at startup. Importing all of it proves the glue's closure stays within the libs.
    Member(
        "mitup-telegram",
        (TELEGRAM, CORE, DATA, MONITORING),
        "import mitup_bot.api_wrapper; import mitup_bot.views; import mitup_bot.utils; "
        "import mitup_bot.guards; import mitup_bot.custom_context; import mitup_bot.mitup_types; "
        "import mitup_bot.reconcile; import mitup_bot.api_guards",
    ),
    Member(
        "mitup-patreon",
        (PATREON, CORE, DATA, MONITORING),
        "import mitup_bot.patreon; import mitup_bot.patreon.client; import mitup_bot.patreon.webhooks; "
        "import mitup_bot.patreon.oauth",
    ),
    Member(
        "mitup-bot-app",
        ("apps/bot", TELEGRAM, PATREON, CORE, DATA, MONITORING),
        "import mitup_bot.app; import mitup_bot.web; import mitup_bot.handlers; "
        "import mitup_bot.bot_cli; import mitup_bot.timezone_api",
    ),
    Member(
        "mitup-events-app",
        ("apps/events", TELEGRAM, PATREON, CORE, DATA, MONITORING),
        "import mitup_bot.events.service; import mitup_bot.events_cli",
    ),
    # The migrations Lambda must resolve without PTB at all.
    Member(
        "mitup-lambda-migrations-app",
        ("apps/lambda-migrations", CORE, DATA, MONITORING),
        "import mitup_bot.lambdas.migrations",
        forbidden_imports=("telegram",),
    ),
    Member(
        "mitup-lambda-alarm-app",
        ("apps/lambda-alarm", CORE),
        "import mitup_bot.lambdas.alarm_action",
        forbidden_imports=("telegram",),
    ),
    Member(
        "mitup-rails-migration",
        ("tools/rails-migration", CORE, DATA, MONITORING),
        "import mitup_bot.migration; import mitup_bot.migration.cli",
        forbidden_imports=("telegram",),
    ),
)


def isolated_argv(repo_root: Path, paths: tuple[str, ...], statement: str) -> list[str]:
    """`uv run` argv that resolves a fresh environment from *paths* alone and runs *statement*.

    `--isolated --no-project` ignores the shared workspace venv (which carries every member's
    dependencies) so only the given paths and their declared PyPI dependencies are available.
    """
    with_args = [argument for path in paths for argument in ("--with", str(repo_root / path))]
    return ["uv", "run", "--isolated", "--no-project", *with_args, "python", "-c", statement]


def check_member(repo_root: Path, member: Member) -> int:
    exit_code = runner.run_step(
        f"Import isolation: {member.name}",
        isolated_argv(repo_root, member.paths, member.imports),
    )
    if exit_code != 0:
        return exit_code
    for forbidden in member.forbidden_imports:
        # A forbidden module must NOT be importable: a zero exit here means the negative proof failed.
        forbidden_exit, _ = runner.run_quiet(isolated_argv(repo_root, member.paths, f"import {forbidden}"))
        if forbidden_exit == 0:
            console.error(f"Import isolation: {member.name} unexpectedly imported `{forbidden}` (must be absent).")
            return 1
        console.success(f"Import isolation: {member.name} confirms `{forbidden}` is absent")
    return 0


def run_check(repo_root: Path) -> int:
    exit_code = 0
    for member in MEMBERS:
        exit_code = check_member(repo_root, member) or exit_code
    return exit_code
