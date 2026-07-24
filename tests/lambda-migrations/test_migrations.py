import os
from collections.abc import Generator
from types import SimpleNamespace
from unittest import mock

import pytest
import structlog
from pydantic import ValidationError
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from mitup_bot.config import MITUP_ENV_VAR, DbConfig, Env
from mitup_bot.lambdas.migrations import (
    APP_DB_PASSWORD_ENV,
    APP_DB_USERNAME_ENV,
    AlembicActions,
    app_role_conninfo,
    app_role_statements,
    run_migrations,
)
from mitup_bot.logging_config import Component


def set_app_role_credentials(monkeypatch: pytest.MonkeyPatch, username: str, password: str):
    monkeypatch.setenv(APP_DB_USERNAME_ENV, username)
    monkeypatch.setenv(APP_DB_PASSWORD_ENV, password)


def clear_app_role_credentials(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(APP_DB_USERNAME_ENV, raising=False)
    monkeypatch.delenv(APP_DB_PASSWORD_ENV, raising=False)


@pytest.fixture(autouse=True)
def mock_configure_logging() -> Generator[mock.MagicMock]:
    """Mock `configure_logging` for every test in this module.

    `configure_logging` mutates process-global root logging state via `basicConfig`. Mocking
    it keeps the migration tests hermetic and lets us assert the lambda wires logging correctly.
    """
    with mock.patch("mitup_bot.lambdas.migrations.configure_logging") as mocked:
        yield mocked


@pytest.fixture(autouse=True)
def dev_environment_variable(monkeypatch: pytest.MonkeyPatch):
    """Seed MITUP_ENV with the local-development value for every test in this module.

    `run_migrations` overwrites the variable in the real process environment; going through
    monkeypatch keeps that assignment from leaking into the rest of the session.
    """
    monkeypatch.setenv(MITUP_ENV_VAR, Env.DEV)


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


def test_sets_prod_environment_before_alembic_runs():
    """Alembic's env.py runs in this same process and reads MITUP_ENV to choose its config
    providers, so the variable must already say prod when the alembic command starts — whatever
    the surrounding environment said (the fixture seeds dev)."""
    environment_during_upgrade: list[str | None] = []

    with mock.patch("mitup_bot.lambdas.migrations.command") as mock_command:
        mock_command.upgrade.side_effect = lambda *_unused: environment_during_upgrade.append(
            os.environ.get(MITUP_ENV_VAR)
        )
        with mock.patch("mitup_bot.lambdas.migrations.Config"):
            run_migrations({"action": "upgrade", "revision": "head"}, None)

    assert environment_during_upgrade == [Env.PROD]


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


def test_wrong_event_logs_before_reraising():
    """A malformed invocation leaves a structured record; the raise still fails the invocation."""
    event = {"action": "myAction", "revision": "myRevision"}

    with capture_logs() as logs:
        with pytest.raises(ValidationError):
            run_migrations(event, None)

    invalid_logs = [log for log in logs if log["event"] == "Invalid migration event"]
    assert len(invalid_logs) == 1
    assert invalid_logs[0]["log_level"] == "error"
    assert invalid_logs[0]["exc_info"] is True
    assert invalid_logs[0]["invocation_event"] == event


def test_migration_failure_logs_with_invocation_context():
    """A failed alembic run logs "Migration failed" with the traceback while flow/action/revision
    are still bound, then re-raises so the Lambda invocation fails."""
    event = {"action": "upgrade", "revision": "abc123"}

    with capture_logs(processors=[merge_contextvars]) as logs:
        with mock.patch("mitup_bot.lambdas.migrations.command") as mock_command:
            with mock.patch("mitup_bot.lambdas.migrations.Config"):
                mock_command.upgrade.side_effect = RuntimeError("boom")
                with pytest.raises(RuntimeError, match="boom"):
                    run_migrations(event, None)

    failed_logs = [log for log in logs if log["event"] == "Migration failed"]
    assert len(failed_logs) == 1
    entry = failed_logs[0]
    assert entry["log_level"] == "error"
    assert entry["exc_info"] is True
    assert entry["flow"] == "migrations"
    assert entry["revision"] == "abc123"


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


def test_app_role_conninfo_strips_async_driver_and_encodes_password():
    """The conninfo drops the +psycopg async suffix and percent-encodes reserved password bytes so
    psycopg's libpq parser reads it back intact."""
    db_config = DbConfig(username="master", password="p@ss:word", url="db.internal", database="mitup", port=5432)

    conninfo = app_role_conninfo(db_config)

    assert conninfo == "postgresql://master:p%40ss%3Aword@db.internal:5432/mitup"


def test_app_role_statements_compose_expected_sql():
    """The composed statements quote the role as an identifier and the password as a literal, and
    cover create-if-absent, password reset, grants, and default privileges — in that order."""
    rendered = [statement.as_string(None) for statement in app_role_statements("mitup_app", "s3cr'et")]

    assert rendered == [
        "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'mitup_app') THEN "
        'CREATE ROLE "mitup_app" WITH LOGIN; END IF; END $$',
        "ALTER ROLE \"mitup_app\" WITH LOGIN PASSWORD 's3cr''et'",
        'GRANT USAGE ON SCHEMA public TO "mitup_app"',
        'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "mitup_app"',
        'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "mitup_app"',
        'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "mitup_app"',
        'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO "mitup_app"',
    ]


@pytest.mark.parametrize(
    ("username", "password"),
    [
        pytest.param(None, None, id="both-unset"),
        pytest.param("   ", "top-secret", id="whitespace-username"),
        pytest.param("mitup_app", None, id="password-unset"),
        pytest.param("mitup_app", "", id="password-empty"),
    ],
)
def test_app_role_bootstrap_skipped_when_credentials_incomplete(
    monkeypatch: pytest.MonkeyPatch, username: str | None, password: str | None
):
    """With the app-role env vars unset, blank, or only partially configured the bootstrap is a
    no-op: no DB connection is opened, the skip is logged, and the alembic command still runs."""
    clear_app_role_credentials(monkeypatch)
    if username is not None:
        monkeypatch.setenv(APP_DB_USERNAME_ENV, username)
    if password is not None:
        monkeypatch.setenv(APP_DB_PASSWORD_ENV, password)

    with capture_logs() as logs:
        with mock.patch("mitup_bot.lambdas.migrations.psycopg") as mock_psycopg:
            with mock.patch("mitup_bot.lambdas.migrations.command") as mock_command:
                with mock.patch("mitup_bot.lambdas.migrations.Config"):
                    run_migrations({"action": "upgrade", "revision": "head"}, None)

    mock_psycopg.connect.assert_not_called()
    mock_command.upgrade.assert_called_once()
    skip_logs = [log for log in logs if log["event"].startswith("App role bootstrap skipped")]
    assert len(skip_logs) == 1


def test_app_role_bootstrap_runs_expected_statements(monkeypatch: pytest.MonkeyPatch):
    """With credentials set, a psycopg connection is opened in autocommit mode and every composed
    statement is executed as the master user; the success line carries the role name."""
    set_app_role_credentials(monkeypatch, "mitup_app", "top-secret")

    cursor = mock.MagicMock(name="cursor")

    with capture_logs() as logs:
        with mock.patch("mitup_bot.lambdas.migrations.psycopg") as mock_psycopg:
            with mock.patch("mitup_bot.lambdas.migrations.MitupConfig"):
                with mock.patch(
                    "mitup_bot.lambdas.migrations.app_role_conninfo", return_value="postgresql://master@db/mitup"
                ):
                    connection = mock_psycopg.connect.return_value.__enter__.return_value
                    connection.cursor.return_value.__enter__.return_value = cursor
                    with mock.patch("mitup_bot.lambdas.migrations.command"):
                        with mock.patch("mitup_bot.lambdas.migrations.Config"):
                            run_migrations({"action": "upgrade", "revision": "head"}, None)

    mock_psycopg.connect.assert_called_once_with("postgresql://master@db/mitup", autocommit=True)
    executed = [call.args[0].as_string(None) for call in cursor.execute.call_args_list]
    expected = [statement.as_string(None) for statement in app_role_statements("mitup_app", "top-secret")]
    assert executed == expected
    ensured_logs = [log for log in logs if log["event"] == "App role ensured"]
    assert len(ensured_logs) == 1
    assert ensured_logs[0]["app_role"] == "mitup_app"


def test_invalid_app_role_name_raises_after_alembic_runs(monkeypatch: pytest.MonkeyPatch):
    """A role name failing the strict pattern fails the invocation (raises) without opening a DB
    connection. The alembic command has already run — the bootstrap follows it."""
    set_app_role_credentials(monkeypatch, "Robert'); DROP", "irrelevant")

    with capture_logs() as logs:
        with mock.patch("mitup_bot.lambdas.migrations.psycopg") as mock_psycopg:
            with mock.patch("mitup_bot.lambdas.migrations.command") as mock_command:
                with mock.patch("mitup_bot.lambdas.migrations.Config"):
                    with pytest.raises(ValueError):
                        run_migrations({"action": "upgrade", "revision": "head"}, None)

    mock_command.upgrade.assert_called_once()
    mock_psycopg.connect.assert_not_called()
    error_logs = [log for log in logs if log["event"] == "App role name is invalid"]
    assert len(error_logs) == 1


def test_app_role_bootstrap_failure_logs_and_propagates(monkeypatch: pytest.MonkeyPatch):
    """A failure while applying the role fails the invocation and logs "App role bootstrap failed"
    with exc_info so the traceback reaches CloudWatch."""
    set_app_role_credentials(monkeypatch, "mitup_app", "top-secret")

    with capture_logs() as logs:
        with mock.patch("mitup_bot.lambdas.migrations.psycopg") as mock_psycopg:
            mock_psycopg.connect.side_effect = RuntimeError("connection refused")
            with mock.patch("mitup_bot.lambdas.migrations.MitupConfig"):
                with mock.patch(
                    "mitup_bot.lambdas.migrations.app_role_conninfo", return_value="postgresql://master@db/mitup"
                ):
                    with mock.patch("mitup_bot.lambdas.migrations.command"):
                        with mock.patch("mitup_bot.lambdas.migrations.Config"):
                            with pytest.raises(RuntimeError, match="connection refused"):
                                run_migrations({"action": "upgrade", "revision": "head"}, None)

    failure_logs = [log for log in logs if log["event"] == "App role bootstrap failed"]
    assert len(failure_logs) == 1
    assert failure_logs[0]["exc_info"] is True


def test_app_role_password_never_appears_in_logs(monkeypatch: pytest.MonkeyPatch):
    """No captured log line — from any phase of the run — contains the app-role password value."""
    password = "sup3r-s3cret-r0tat3d"
    set_app_role_credentials(monkeypatch, "mitup_app", password)

    cursor = mock.MagicMock(name="cursor")

    with capture_logs() as logs:
        with mock.patch("mitup_bot.lambdas.migrations.psycopg") as mock_psycopg:
            with mock.patch("mitup_bot.lambdas.migrations.MitupConfig"):
                with mock.patch(
                    "mitup_bot.lambdas.migrations.app_role_conninfo", return_value="postgresql://master@db/mitup"
                ):
                    connection = mock_psycopg.connect.return_value.__enter__.return_value
                    connection.cursor.return_value.__enter__.return_value = cursor
                    with mock.patch("mitup_bot.lambdas.migrations.command"):
                        with mock.patch("mitup_bot.lambdas.migrations.Config"):
                            run_migrations({"action": "upgrade", "revision": "head"}, None)

    assert not any(password in str(value) for log in logs for value in log.values())


def test_app_role_bootstrap_runs_after_alembic_command(monkeypatch: pytest.MonkeyPatch):
    """Ordering guarantee: the alembic upgrade runs before the psycopg connection is opened, so the
    grants cover tables the migration just created."""
    set_app_role_credentials(monkeypatch, "mitup_app", "top-secret")

    manager = mock.Mock()
    cursor = mock.MagicMock(name="cursor")

    with mock.patch("mitup_bot.lambdas.migrations.psycopg") as mock_psycopg:
        manager.attach_mock(mock_psycopg.connect, "connect")
        connection = mock_psycopg.connect.return_value.__enter__.return_value
        connection.cursor.return_value.__enter__.return_value = cursor
        with mock.patch("mitup_bot.lambdas.migrations.MitupConfig"):
            with mock.patch(
                "mitup_bot.lambdas.migrations.app_role_conninfo", return_value="postgresql://master@db/mitup"
            ):
                with mock.patch("mitup_bot.lambdas.migrations.command") as mock_command:
                    manager.attach_mock(mock_command.upgrade, "upgrade")
                    with mock.patch("mitup_bot.lambdas.migrations.Config"):
                        run_migrations({"action": "upgrade", "revision": "head"}, None)

    ordered = [call[0] for call in manager.mock_calls if call[0] in {"upgrade", "connect"}]
    assert ordered == ["upgrade", "connect"]
