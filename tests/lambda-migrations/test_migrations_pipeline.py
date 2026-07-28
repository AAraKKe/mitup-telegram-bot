import io
import json
import logging
import sys
from collections.abc import Generator
from unittest import mock

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from mitup_bot.config import MITUP_ENV_VAR, Env
from mitup_bot.lambdas.migrations import APP_DB_PASSWORD_ENV, APP_DB_USERNAME_ENV, run_migrations
from tests.helpers import drop_cached_logger_binds

UPGRADE_EVENT = {"action": "upgrade", "revision": "head"}


@pytest.fixture(autouse=True)
def restore_logging_state() -> Generator[None]:
    """`run_migrations` calls the production `configure_logging`, which mutates the root logger's
    handlers and level. Snapshot and restore so this module doesn't leak into the rest of the suite.
    """
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    try:
        yield
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)
        drop_cached_logger_binds()


@pytest.fixture(autouse=True)
def lambda_environment(monkeypatch: pytest.MonkeyPatch):
    """Give env.py a complete `DbConfig` (offline mode never connects) and leave the app-role
    credentials unset so the bootstrap that follows the Alembic run is a no-op. `run_migrations`
    assigns MITUP_ENV in the real process environment, so monkeypatch owns it here.
    """
    for name in ("USERNAME", "PASSWORD", "DATABASE"):
        monkeypatch.setenv(f"MITUPBOT__DB__{name}", "migrations-pipeline-test")
    monkeypatch.setenv("MITUPBOT__DB__URL", "localhost")
    monkeypatch.setenv("MITUPBOT__DB__PORT", "5432")
    monkeypatch.setenv(MITUP_ENV_VAR, Env.DEV)
    monkeypatch.delenv(APP_DB_USERNAME_ENV, raising=False)
    monkeypatch.delenv(APP_DB_PASSWORD_ENV, raising=False)


def offline_upgrade(alembic_config: Config, revision: str):
    """Stand in for the Lambda's online upgrade with an offline one over the same env.py.

    env.py decides how to set logging up at module level, before the offline/online branch, so this
    reaches the code under test without a database. The range is head-to-head, so no revision script
    executes and `revision` is only reported back for the assertion.
    """
    head = ScriptDirectory.from_config(alembic_config).get_current_head()
    assert head is not None, "the project must have at least one revision"
    assert revision == "head"
    command.upgrade(alembic_config, f"{head}:{head}", sql=True)


def run_lambda_capturing_stderr() -> list[dict[str, object]]:
    """Run the Lambda handler end to end over the real `alembic.ini`, returning every structured
    line it wrote. The root handler binds `sys.stderr` when `configure_logging` builds it, so
    replacing the stream first is what makes the rendered output readable back."""
    buffer = io.StringIO()
    with mock.patch("mitup_bot.lambdas.migrations.command") as patched_command:
        patched_command.upgrade.side_effect = offline_upgrade
        with mock.patch.object(sys, "stderr", buffer):
            run_migrations(UPGRADE_EVENT, None)

    return [json.loads(line) for line in buffer.getvalue().splitlines() if line.startswith("{")]


def test_migration_outcome_survives_a_real_alembic_run():
    """Everything the Lambda logs comes after Alembic returns — the outcome line and the whole
    `bootstrap_app_role` trail. Alembic's own logging setup runs in this process, so an ini file
    applied with the disabling default leaves a failed production migration with no record at all.
    """
    records = run_lambda_capturing_stderr()

    events = [str(record["event"]) for record in records]
    assert "Migration completed" in events
    assert any(event.startswith("App role bootstrap skipped") for event in events)
