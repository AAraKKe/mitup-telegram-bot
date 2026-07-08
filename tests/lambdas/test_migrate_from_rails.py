from collections.abc import Generator
from types import SimpleNamespace
from unittest import mock

import pytest
import structlog
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from mitup_bot.cli.commands.migrate_from_rails import ALL_PHASES
from mitup_bot.lambdas.migrate_from_rails import handler

ALL_PHASES_CSV = ",".join(ALL_PHASES)


@pytest.fixture(autouse=True)
def mock_configure_logging() -> Generator[mock.MagicMock]:
    """Mock `configure_logging` for every test in this module.

    `configure_logging` mutates process-global root logging state via `basicConfig`. Mocking it
    keeps the lambda tests hermetic.
    """
    with mock.patch("mitup_bot.lambdas.migrate_from_rails.configure_logging") as mocked:
        yield mocked


@pytest.fixture
def mock_rails_work() -> Generator[tuple[mock.MagicMock, mock.MagicMock]]:
    """Stub the two pieces of real work the handler performs: the Secrets Manager DSN lookup and
    the CLI invocation. Both run inside the bound_contextvars block, so a log emitted from either
    side-effect sees the invocation contextvars."""
    with (
        mock.patch("mitup_bot.lambdas.migrate_from_rails.fetch_rails_dsn", return_value="postgres://rails") as fetch,
        mock.patch("mitup_bot.lambdas.migrate_from_rails.invoke_from_lambda") as invoke,
    ):
        yield fetch, invoke


def emit_inside(message: str):
    """Return a side_effect that emits a log from inside the handler's bound block."""

    def side_effect(*_args: object, **_kwargs: object):
        structlog.get_logger("mitup_bot").info(message)

    return side_effect


def test_binds_invocation_contextvars_during_handler_body(
    mock_rails_work: tuple[mock.MagicMock, mock.MagicMock],
    monkeypatch: pytest.MonkeyPatch,
):
    """The handler binds flow/dry_run/phases for the duration of the body, so logs emitted while
    the migration runs carry the invocation context."""
    monkeypatch.setenv("RAILS_DB_SECRET_ARN", "arn:secret")
    _fetch, invoke = mock_rails_work
    invoke.side_effect = emit_inside("Running Rails migration")

    with capture_logs(processors=[merge_contextvars]) as logs:
        handler({"dry_run": False, "phases": "users,meetups"}, None)

    running = [log for log in logs if log["event"] == "Running Rails migration"]
    assert len(running) == 1
    entry = running[0]
    assert entry["flow"] == "migrate_from_rails"
    assert "lambda" not in entry
    assert entry["dry_run"] is False
    assert entry["phases"] == "users,meetups"


def test_dry_run_defaults_to_true_when_absent(
    mock_rails_work: tuple[mock.MagicMock, mock.MagicMock],
    monkeypatch: pytest.MonkeyPatch,
):
    """The lambda defaults dry_run to True for safety; the bound contextvar reflects that, and
    phases default to every phase when not supplied."""
    monkeypatch.setenv("RAILS_DB_SECRET_ARN", "arn:secret")
    _fetch, invoke = mock_rails_work
    invoke.side_effect = emit_inside("Running Rails migration")

    with capture_logs(processors=[merge_contextvars]) as logs:
        handler({}, None)

    entry = next(log for log in logs if log["event"] == "Running Rails migration")
    assert entry["dry_run"] is True
    assert entry["phases"] == ALL_PHASES_CSV


def test_includes_aws_request_id_when_context_has_it(
    mock_rails_work: tuple[mock.MagicMock, mock.MagicMock],
    monkeypatch: pytest.MonkeyPatch,
):
    """When the AWS context arg exposes aws_request_id, it is bound alongside the other fields."""
    monkeypatch.setenv("RAILS_DB_SECRET_ARN", "arn:secret")
    _fetch, invoke = mock_rails_work
    invoke.side_effect = emit_inside("Running Rails migration")
    context = SimpleNamespace(aws_request_id="req-abc")

    with capture_logs(processors=[merge_contextvars]) as logs:
        handler({"dry_run": True}, context)

    entry = next(log for log in logs if log["event"] == "Running Rails migration")
    assert entry["aws_request_id"] == "req-abc"


def test_omits_aws_request_id_when_context_lacks_it(
    mock_rails_work: tuple[mock.MagicMock, mock.MagicMock],
    monkeypatch: pytest.MonkeyPatch,
):
    """The hasattr guard omits aws_request_id when the context arg doesn't carry one (e.g. None)."""
    monkeypatch.setenv("RAILS_DB_SECRET_ARN", "arn:secret")
    _fetch, invoke = mock_rails_work
    invoke.side_effect = emit_inside("Running Rails migration")

    with capture_logs(processors=[merge_contextvars]) as logs:
        handler({"dry_run": True}, None)

    entry = next(log for log in logs if log["event"] == "Running Rails migration")
    assert "aws_request_id" not in entry


def test_clears_invocation_contextvars_after_return(
    mock_rails_work: tuple[mock.MagicMock, mock.MagicMock],
    monkeypatch: pytest.MonkeyPatch,
):
    """bound_contextvars auto-clears on exit, so a log emitted after the handler returns carries
    none of the invocation fields."""
    monkeypatch.setenv("RAILS_DB_SECRET_ARN", "arn:secret")

    with capture_logs(processors=[merge_contextvars]) as logs:
        handler({"dry_run": True}, None)
        structlog.get_logger("mitup_bot").info("after handler")

    entry = next(log for log in logs if log["event"] == "after handler")
    for field in ("flow", "dry_run", "phases", "aws_request_id"):
        assert field not in entry
