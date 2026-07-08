from collections.abc import Generator
from types import SimpleNamespace
from unittest import mock

import pytest
import structlog
from pydantic import ValidationError
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from mitup_bot.config import Env
from mitup_bot.lambdas.migrations import AlembicActions, run_migrations
from mitup_bot.logging_config import Component


@pytest.fixture(autouse=True)
def mock_configure_logging() -> Generator[mock.MagicMock]:
    """Mock `configure_logging` for every test in this module.

    `configure_logging` mutates process-global root logging state via `basicConfig`. Mocking
    it keeps the migration tests hermetic and lets us assert the lambda wires logging correctly.
    """
    with mock.patch("mitup_bot.lambdas.migrations.configure_logging") as mocked:
        yield mocked


def test_upgrade():
    with mock.patch("mitup_bot.lambdas.migrations.command") as mock_command:
        with mock.patch("mitup_bot.lambdas.migrations.Config") as mock_alembic_config:
            event = {"action": "upgrade", "revision": "myRevision"}
            alembic_config = mock.MagicMock(name="AlembicConfigMock")
            mock_alembic_config.return_value = alembic_config

            run_migrations(event, None)

            mock_command.upgrade.assert_called_once_with(alembic_config, "myRevision")
            mock_command.downgrade.assert_not_called()


def test_downgrade():
    with mock.patch("mitup_bot.lambdas.migrations.command") as mock_command:
        with mock.patch("mitup_bot.lambdas.migrations.Config") as mock_alembic_config:
            event = {"action": "downgrade", "revision": "myRevision"}
            alembic_config = mock.MagicMock(name="AlembicConfigMock")
            mock_alembic_config.return_value = alembic_config

            run_migrations(event, None)

            mock_command.downgrade.assert_called_once_with(alembic_config, "myRevision")
            mock_command.upgrade.assert_not_called()


def test_configures_logging_with_default_level_when_env_unset(
    mock_configure_logging: mock.MagicMock,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    with mock.patch("mitup_bot.lambdas.migrations.command"):
        with mock.patch("mitup_bot.lambdas.migrations.Config"):
            run_migrations({"action": "upgrade", "revision": "myRevision"}, None)

    mock_configure_logging.assert_called_once_with(Env.PROD, Component.LAMBDA, "INFO")


def test_configures_logging_with_log_level_env(
    mock_configure_logging: mock.MagicMock,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    with mock.patch("mitup_bot.lambdas.migrations.command"):
        with mock.patch("mitup_bot.lambdas.migrations.Config"):
            run_migrations({"action": "upgrade", "revision": "myRevision"}, None)

    mock_configure_logging.assert_called_once_with(Env.PROD, Component.LAMBDA, "DEBUG")


def test_configures_logging_before_migration_work(
    mock_configure_logging: mock.MagicMock,
):
    # configure_logging must run before any alembic command so migration output is captured.
    manager = mock.Mock()
    manager.attach_mock(mock_configure_logging, "configure_logging")
    with mock.patch("mitup_bot.lambdas.migrations.command") as mock_command:
        manager.attach_mock(mock_command.upgrade, "upgrade")
        with mock.patch("mitup_bot.lambdas.migrations.Config"):
            run_migrations({"action": "upgrade", "revision": "myRevision"}, None)

    assert [call[0] for call in manager.mock_calls] == ["configure_logging", "upgrade"]


def test_wrong_event_fails():
    event = {"action": "myAction", "revision": "myRevision"}

    with pytest.raises(ValidationError) as exc_info:
        run_migrations(event, None)

    assert exc_info.value.error_count() == 1

    error = exc_info.value.errors()[0]
    # Validate that there was an error parsing action, as it is not recognized in the enum
    assert error["type"] == "enum"
    assert error["loc"][0] == "action"
    assert error["input"] == "myAction"


def test_binds_invocation_contextvars_during_handler_body():
    """run_migrations binds flow/action/revision for the duration of the handler so the
    "Migration started" / "Migration completed" logs (and any alembic logging) carry the
    invocation context."""
    event = {"action": "upgrade", "revision": "abc123"}

    with capture_logs(processors=[merge_contextvars]) as logs:
        with mock.patch("mitup_bot.lambdas.migrations.command"):
            with mock.patch("mitup_bot.lambdas.migrations.Config"):
                run_migrations(event, None)

    start_logs = [log for log in logs if log["event"] == "Migration started"]
    assert len(start_logs) == 1
    entry = start_logs[0]
    assert entry["flow"] == "migrations"
    assert "lambda" not in entry
    assert entry["action"] == AlembicActions.UPGRADE  # bound as the enum, str-equals "upgrade"
    assert entry["revision"] == "abc123"


def test_includes_aws_request_id_when_context_has_it():
    """When the AWS context arg exposes aws_request_id, it is bound alongside the other fields."""
    context = SimpleNamespace(aws_request_id="req-123")

    with capture_logs(processors=[merge_contextvars]) as logs:
        with mock.patch("mitup_bot.lambdas.migrations.command"):
            with mock.patch("mitup_bot.lambdas.migrations.Config"):
                run_migrations({"action": "upgrade", "revision": "abc123"}, context)

    start_logs = [log for log in logs if log["event"] == "Migration started"]
    assert len(start_logs) == 1
    assert start_logs[0]["aws_request_id"] == "req-123"


def test_omits_aws_request_id_when_context_lacks_it():
    """The hasattr guard omits aws_request_id when the context arg doesn't carry one (e.g. None)."""
    with capture_logs(processors=[merge_contextvars]) as logs:
        with mock.patch("mitup_bot.lambdas.migrations.command"):
            with mock.patch("mitup_bot.lambdas.migrations.Config"):
                run_migrations({"action": "upgrade", "revision": "abc123"}, None)

    start_logs = [log for log in logs if log["event"] == "Migration started"]
    assert len(start_logs) == 1
    assert "aws_request_id" not in start_logs[0]


def test_clears_invocation_contextvars_after_return():
    """bound_contextvars auto-clears on exit, so a log emitted after run_migrations returns carries
    none of the invocation fields."""
    with capture_logs(processors=[merge_contextvars]) as logs:
        with mock.patch("mitup_bot.lambdas.migrations.command"):
            with mock.patch("mitup_bot.lambdas.migrations.Config"):
                run_migrations({"action": "upgrade", "revision": "abc123"}, None)
        structlog.get_logger("mitup_bot").info("after migration")

    after_logs = [log for log in logs if log["event"] == "after migration"]
    assert len(after_logs) == 1
    entry = after_logs[0]
    for field in ("flow", "action", "revision", "aws_request_id"):
        assert field not in entry
