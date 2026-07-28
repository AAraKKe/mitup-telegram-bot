import io
import json
import logging
from collections.abc import Generator
from typing import cast

import pytest
import structlog
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from mitup_bot.config import Env
from mitup_bot.logging_config import Component, configure_logging
from mitup_bot.migrations.logging_setup import ALEMBIC_LOGGER_NAME, configure_migration_logging
from tests.helpers import drop_cached_logger_binds

ALEMBIC_INI = "alembic.ini"


@pytest.fixture(autouse=True)
def restore_logging_state() -> Generator[None]:
    """`configure_logging` and `fileConfig` both mutate process-global state (root level and
    handlers, plus per-library levels). Snapshot and restore so these tests don't leak into the
    rest of the suite.
    """
    root = logging.getLogger()
    saved_root_handlers = root.handlers[:]
    saved_root_level = root.level
    saved_alembic_level = logging.getLogger(ALEMBIC_LOGGER_NAME).level
    try:
        yield
    finally:
        root.handlers[:] = saved_root_handlers
        root.setLevel(saved_root_level)
        logging.getLogger(ALEMBIC_LOGGER_NAME).setLevel(saved_alembic_level)
        drop_cached_logger_binds()


@pytest.fixture(autouse=True)
def offline_db_credentials(monkeypatch: pytest.MonkeyPatch):
    """env.py builds a `DbConfig` at import time. Offline mode never opens a connection, so any
    complete set of values is enough to let the real env.py run without a database."""
    for name in ("USERNAME", "PASSWORD", "DATABASE"):
        monkeypatch.setenv(f"MITUPBOT__DB__{name}", "migration-logging-test")
    monkeypatch.setenv("MITUPBOT__DB__URL", "localhost")
    monkeypatch.setenv("MITUPBOT__DB__PORT", "5432")


def run_alembic_over_real_env_py(span_last_revision: bool = False):
    """Drive a real Alembic run through the project's own env.py, without a database.

    env.py sets logging up at module level, before the offline/online branch, so the offline path
    makes exactly the same logging decision an upgrade in the migrations Lambda does. The range is
    head-to-head by default, so no revision script executes; *span_last_revision* widens it by one
    so Alembic emits its "Running upgrade" line. Widening requires the head revision to be
    renderable as static SQL, which a revision binding a non-literal default is not.
    """
    config = Config(ALEMBIC_INI)
    script_directory = ScriptDirectory.from_config(config)
    head = script_directory.get_current_head()
    assert head is not None, "the project must have at least one revision"
    start = script_directory.get_revision(head).down_revision if span_last_revision else head
    command.upgrade(config, f"{start}:{head}", sql=True)


def redirect_pipeline_to_buffer() -> io.StringIO:
    """Point the installed root handler at a buffer so assertions read exactly what the configured
    processor chain produced."""
    handler = cast("logging.StreamHandler[io.StringIO]", logging.getLogger().handlers[0])
    buffer = io.StringIO()
    handler.setStream(buffer)
    return buffer


def rendered_records(buffer: io.StringIO) -> list[dict[str, object]]:
    logging.getLogger().handlers[0].flush()
    return [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]


def test_configured_pipeline_survives_an_alembic_run():
    """The migrations Lambda logs its outcome *after* Alembic returns. Applying alembic.ini's
    logging config would disable every logger already built in the process, so this asserts the
    post-run line still reaches the configured pipeline."""
    configure_logging(Env.PROD, Component.LAMBDA)
    log = structlog.get_logger("probe.migrations")
    log.info("before alembic")
    buffer = redirect_pipeline_to_buffer()

    run_alembic_over_real_env_py()
    log.info("after alembic")

    events = [record["event"] for record in rendered_records(buffer)]
    assert "after alembic" in events


def test_alembic_progress_renders_through_the_configured_pipeline():
    """Alembic's "Running upgrade" line is the record of which revision ran, so it must land in the
    structured pipeline as a queryable field rather than on a plain-text stderr handler of its own."""
    configure_logging(Env.PROD, Component.LAMBDA)
    buffer = redirect_pipeline_to_buffer()

    run_alembic_over_real_env_py(span_last_revision=True)

    alembic_records = [
        record for record in rendered_records(buffer) if str(record.get("logger", "")).startswith(ALEMBIC_LOGGER_NAME)
    ]
    assert any(str(record["event"]).startswith("Running upgrade") for record in alembic_records)
    assert all(record["component"] == Component.LAMBDA for record in alembic_records)


def test_file_config_applies_and_spares_existing_loggers_without_structlog():
    """A bare `alembic` CLI run has no structured pipeline to defer to, so alembic.ini still
    applies — but never with the default that disables loggers already built in the process."""
    structlog.reset_defaults()
    probe = logging.getLogger("probe.bare-cli")

    configure_migration_logging(ALEMBIC_INI)

    assert probe.disabled is False
    assert logging.getLogger(ALEMBIC_LOGGER_NAME).level == logging.INFO
