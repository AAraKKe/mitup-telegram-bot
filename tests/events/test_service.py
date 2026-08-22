import asyncio
import json
import signal
from asyncio import CancelledError
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog
from aws_embedded_metrics.environment import Environment
from aws_embedded_metrics.environment.local_environment import LocalEnvironment
from aws_embedded_metrics.logger.metrics_context import MetricsContext
from aws_embedded_metrics.serializers.log_serializer import LogSerializer
from aws_embedded_metrics.sinks import Sink
from click.testing import CliRunner
from pydantic import SecretStr
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.config import BotConfig
from mitup_bot.events.service import (
    DEFAULT_USER_CLEANUP_INTERVAL,
    EventType,
    IntervalsConfiguration,
    ShutdownRequest,
    StopReason,
    build_bot,
    build_broadcast_bot,
    dispatch_event,
    handle_maintainance,
    request_shutdown,
    run_all_tasks,
    run_periodic,
    select_bot,
)
from mitup_bot.events_cli import recurrent_events as cli
from mitup_bot.models.subscriptions import TokenCipher
from mitup_bot.monitoring import (
    EmfBackend,
    MetricKey,
    MetricsBackend,
    MetricsClient,
    MetricUnit,
    NullBackend,
    current_metrics_client,
)
from mitup_bot.patreon.runtime import PatreonRuntime
from tests.helpers import MockApi, create_patreon_config
from tests.helpers.monitoring import MetricAssertions, make_test_metrics_client

INTERVAL_PARAMS = [
    (EventType.USER_CLEANUP, "user_cleanup"),
    (EventType.NOTIFY_START_MEETING, "notify_start_meeting"),
    (EventType.NOTIFY_MEETING_STARTED, "notify_meeting_started"),
    (EventType.GENERATE_STATS, "generate_stats"),
    (EventType.DEACTIVATE_MEETINGS, "deactivate_meetings"),
    (EventType.MEETUPS_CLEANUP, "meetups_cleanup"),
    (EventType.SEND_BROADCASTS, "send_broadcasts"),
    (EventType.SUPPORTER_CHECK, "supporter_check"),
]


@pytest.mark.parametrize(
    "event_type, field_name",
    INTERVAL_PARAMS,
    ids=[e.name for e, _ in INTERVAL_PARAMS],
)
def test_intervals_configuration_get(event_type: EventType, field_name: str):
    config = IntervalsConfiguration(
        user_cleanup=10,
        notify_start_meeting=20,
        notify_meeting_started=25,
        generate_stats=30,
        deactivate_meetings=40,
        meetups_cleanup=50,
        send_broadcasts=60,
        supporter_check=60,
    )
    assert config.get(event_type) == getattr(config, field_name)


@patch("mitup_bot.events.service.ExtBot")
def test_build_bot(mock_ext_bot: MagicMock):
    bot_config = BotConfig(token=SecretStr("test-token"), retries_on_throttle=5, api_read_timeout=1.5)

    build_bot(bot_config)

    mock_ext_bot.assert_called_once()
    call_kwargs = mock_ext_bot.call_args.kwargs
    assert call_kwargs["token"] == "test-token"
    assert call_kwargs["rate_limiter"] is not None
    assert "defaults" not in call_kwargs
    # The events bots talk to Telegram under the same timeouts as the bot app, so a stalled call
    # is given up on there too rather than sitting on PTB's longer defaults.
    assert call_kwargs["request"]._client.timeout.read == 1.5


@patch("mitup_bot.events.service.AIORateLimiter")
@patch("mitup_bot.events.service.ExtBot")
def test_build_broadcast_bot_applies_configured_rate(mock_ext_bot: MagicMock, mock_rate_limiter: MagicMock):
    bot_config = BotConfig(
        token=SecretStr("test-token"), retries_on_throttle=3, broadcast_max_rate=7, api_read_timeout=1.5
    )

    build_broadcast_bot(bot_config)

    # The broadcast bot caps its send rate at broadcast_max_rate per second while sharing the
    # retry budget with the main bot.
    mock_rate_limiter.assert_called_once_with(overall_max_rate=7, overall_time_period=1, max_retries=3)
    mock_ext_bot.assert_called_once()
    call_kwargs = mock_ext_bot.call_args.kwargs
    assert call_kwargs["token"] == "test-token"
    assert call_kwargs["rate_limiter"] is mock_rate_limiter.return_value
    assert call_kwargs["request"]._client.timeout.read == 1.5


@pytest.mark.parametrize(
    "event_type", [event for event in EventType if event is not EventType.SEND_BROADCASTS], ids=lambda e: e.name
)
def test_select_bot_uses_shared_bot_for_non_broadcast_events(event_type: EventType):
    bot, broadcast_bot = MagicMock(), MagicMock()

    assert select_bot(event_type, bot, broadcast_bot) is bot


def test_select_bot_uses_broadcast_bot_for_send_broadcasts():
    bot, broadcast_bot = MagicMock(), MagicMock()

    assert select_bot(EventType.SEND_BROADCASTS, bot, broadcast_bot) is broadcast_bot


# Async event types use `await module.run(...)`, sync ones call directly.
ASYNC_LAUNCH_PARAMS = [
    (EventType.NOTIFY_START_MEETING, "mitup_bot.events.service.notify_meetings"),
    (EventType.NOTIFY_MEETING_STARTED, "mitup_bot.events.service.notify_meetings_started"),
    (EventType.DEACTIVATE_MEETINGS, "mitup_bot.events.service.inactive_meetings"),
    (EventType.MEETUPS_CLEANUP, "mitup_bot.events.service.meetups_cleanup"),
    (EventType.SUPPORTER_CHECK, "mitup_bot.events.service.supporter_check"),
]

SYNC_LAUNCH_PARAMS = [
    (EventType.USER_CLEANUP, "mitup_bot.events.service.user_cleanup"),
    (EventType.GENERATE_STATS, "mitup_bot.events.service.generate_stats"),
]


@pytest.mark.parametrize(
    "event_type, module_path",
    ASYNC_LAUNCH_PARAMS,
    ids=[e.name for e, _ in ASYNC_LAUNCH_PARAMS],
)
async def test_dispatch_event_async(event_type: EventType, module_path: str):
    api = MockApi()
    client = make_test_metrics_client()

    with patch(f"{module_path}.run", new_callable=AsyncMock) as mock_run:
        await dispatch_event(event_type, api, client, [])
        mock_run.assert_awaited_once_with(api, client)


async def test_dispatch_event_send_broadcasts():
    """SEND_BROADCASTS dispatches send_broadcasts.run with the operator allowlist appended."""
    api = MockApi()
    client = make_test_metrics_client()
    admin_tg_ids = [111, 222]

    with patch("mitup_bot.events.service.broadcast.run", new_callable=AsyncMock) as mock_run:
        await dispatch_event(EventType.SEND_BROADCASTS, api, client, admin_tg_ids)
        mock_run.assert_awaited_once_with(api, client, admin_tg_ids)


@pytest.mark.parametrize(
    "event_type, module_path",
    SYNC_LAUNCH_PARAMS,
    ids=[e.name for e, _ in SYNC_LAUNCH_PARAMS],
)
async def test_dispatch_event_sync(event_type: EventType, module_path: str):
    api = MockApi()
    client = make_test_metrics_client()

    with patch(f"{module_path}.run") as mock_run:
        await dispatch_event(event_type, api, client, [])
        mock_run.assert_called_once_with(api, client)


async def run_maintainance_with_mocked_db(event_type: EventType, client: MetricsClient):
    """Run handle_maintainance with the db module mocked out, for tests that only care about
    logging side effects."""
    with patch("mitup_bot.events.service.db") as mock_db:
        mock_db.get_open_connections.return_value = 0
        await handle_maintainance(event_type, MagicMock(), [], client=client)


async def test_handle_maintainance_binds_event_contextvars():
    """A log emitted while an event runs carries the dispatched EventType.value and a run_id, bound
    by handle_maintainance for the duration of the run.

    `flow` is the plane's only name for the job: it is the key the cross-service infra queries
    group by, and the metric plane already carries the value as the EventType dimension, so no
    second log key holds it on every line the plane emits."""
    client = make_test_metrics_client()

    def run_emitting_log(_api: object, _client: object):
        structlog.get_logger("mitup_bot").info("event running")

    with capture_logs(processors=[merge_contextvars]) as logs:
        with patch("mitup_bot.events.service.user_cleanup.run", side_effect=run_emitting_log):
            await run_maintainance_with_mocked_db(EventType.USER_CLEANUP, client)

    event_logs = [log for log in logs if log["event"] == "event running"]
    assert len(event_logs) == 1
    entry = event_logs[0]
    assert entry["flow"] == EventType.USER_CLEANUP.value  # "UserCleanup"
    assert "event_type" not in entry
    # run_id is a uuid4().hex — present and a 32-char hex string, but its exact value is random.
    run_id = entry["run_id"]
    assert isinstance(run_id, str)
    assert len(run_id) == 32  # uuid4().hex


async def test_handle_maintainance_clears_contextvars_between_events():
    """bound_contextvars auto-clears on exit, so flow/run_id must not leak from one event into a log
    emitted after handle_maintainance returns (events run back-to-back in run_periodic)."""
    client = make_test_metrics_client()

    with capture_logs(processors=[merge_contextvars]) as logs:
        with patch("mitup_bot.events.service.user_cleanup.run"):
            await run_maintainance_with_mocked_db(EventType.USER_CLEANUP, client)
        # Emitted after handle_maintainance returned — must carry neither event field.
        structlog.get_logger("mitup_bot").info("between events")

    after_logs = [log for log in logs if log["event"] == "between events"]
    assert len(after_logs) == 1
    entry = after_logs[0]
    assert "flow" not in entry
    assert "event_type" not in entry
    assert "run_id" not in entry


async def test_handle_maintainance_uses_distinct_run_id_per_invocation():
    """Each run generates a fresh run_id so back-to-back events are distinguishable."""
    client = make_test_metrics_client()

    def capture_run_id(_api: object, _client: object):
        structlog.get_logger("mitup_bot").info("event running")

    with capture_logs(processors=[merge_contextvars]) as logs:
        with patch("mitup_bot.events.service.user_cleanup.run", side_effect=capture_run_id):
            await run_maintainance_with_mocked_db(EventType.USER_CLEANUP, client)
            await run_maintainance_with_mocked_db(EventType.USER_CLEANUP, client)

    run_ids = [log["run_id"] for log in logs if log["event"] == "event running"]
    assert len(run_ids) == 2
    assert run_ids[0] != run_ids[1]


async def test_handle_maintainance_fault_logs_exception_under_run_context():
    """A failing run produces one structlog error line with the exception attached, emitted while
    flow/run_id are still bound — the log-side half of fault triage."""
    client = make_test_metrics_client()

    with capture_logs(processors=[merge_contextvars]) as logs:
        with patch(
            "mitup_bot.events.service.dispatch_event",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            await run_maintainance_with_mocked_db(EventType.USER_CLEANUP, client)

    fault_logs = [log for log in logs if log["event"] == "Recurrent event run failed"]
    assert len(fault_logs) == 1
    entry = fault_logs[0]
    assert entry["log_level"] == "error"
    assert entry["exc_info"] is True  # log.exception attaches the active exception
    assert entry["flow"] == EventType.USER_CLEANUP.value
    assert len(entry["run_id"]) == 32
    assert entry["outcome"] == "failed"
    assert entry["error_type"] == "builtins.RuntimeError"
    assert entry["duration_ms"] >= 0


async def test_handle_maintainance_brackets_a_clean_run_with_start_and_finish():
    """Every run opens with a start line and closes with a finish line, both under the run bind.

    This is the anchor the infra saved query tells operators to copy `run_id` from: six of the eight
    jobs emit nothing at all on a clean run, so without these two an empty result for a `run_id` is
    ambiguous between "never ran" and "ran and decided nothing". Asserted once here — the bracket is
    one mechanism in `handle_maintainance`, not something each job repeats.
    """
    client = make_test_metrics_client()

    with capture_logs(processors=[merge_contextvars]) as logs:
        with patch("mitup_bot.events.service.user_cleanup.run"):
            await run_maintainance_with_mocked_db(EventType.USER_CLEANUP, client)

    started = next(log for log in logs if log["event"] == "Recurrent event run started")
    finished = next(log for log in logs if log["event"] == "Recurrent event run finished")
    assert started["flow"] == EventType.USER_CLEANUP.value
    assert started["run_id"] == finished["run_id"]
    assert finished["outcome"] == "completed"
    assert finished["duration_ms"] >= 0
    assert finished["db_connections_leaked"] == 0


async def test_handle_maintainance_finish_line_reports_a_faulted_run():
    """A run whose job raised still closes — the finish line is emitted from the `finally`, so the
    series of run brackets is continuous regardless of exit path, and it carries the failure."""
    client = make_test_metrics_client()

    with capture_logs(processors=[merge_contextvars]) as logs:
        with patch("mitup_bot.events.service.dispatch_event", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            await run_maintainance_with_mocked_db(EventType.USER_CLEANUP, client)

    finished = next(log for log in logs if log["event"] == "Recurrent event run finished")
    assert finished["outcome"] == "failed"


async def test_handle_maintainance_finish_line_reports_a_run_the_shutdown_interrupted():
    """A run cancelled by a stop signal still closes its bracket, but must not read as a completed
    one — a rolling deploy would otherwise look like a clean tick of every job it cut short."""
    client = make_test_metrics_client()

    with capture_logs(processors=[merge_contextvars]) as logs:
        with patch("mitup_bot.events.service.dispatch_event", new_callable=AsyncMock, side_effect=CancelledError):
            with pytest.raises(CancelledError):
                await run_maintainance_with_mocked_db(EventType.USER_CLEANUP, client)

    finished = next(log for log in logs if log["event"] == "Recurrent event run finished")
    assert finished["outcome"] == "interrupted"


async def test_run_all_tasks_logs_a_registration_line_per_event():
    """Each scheduled task announces its event type, interval and which bot it runs on, so the
    schedule a container is actually running is readable from its own log."""
    intervals = IntervalsConfiguration(
        user_cleanup=10,
        notify_start_meeting=20,
        notify_meeting_started=25,
        generate_stats=30,
        deactivate_meetings=40,
        meetups_cleanup=50,
        send_broadcasts=60,
        supporter_check=70,
    )

    with capture_logs() as logs:
        with patch("mitup_bot.events.service.run_periodic", new_callable=AsyncMock):
            await run_all_tasks(intervals, MagicMock(), MagicMock(), [], start_time=0.0)

    registered = {log["flow"]: log for log in logs if log["event"] == "Registered recurrent event"}
    assert set(registered) == {event_type.value for event_type in EventType}
    assert registered[EventType.USER_CLEANUP.value]["interval_seconds"] == 10
    assert registered[EventType.USER_CLEANUP.value]["bot"] == "shared"
    # Only SEND_BROADCASTS runs on the separately rate-capped bot.
    assert registered[EventType.SEND_BROADCASTS.value]["bot"] == "broadcast"


async def test_run_all_tasks_logs_why_scheduling_stopped():
    """`handle_maintainance` swallows Exception, so the only thing that stops the loops is a
    BaseException that takes every recurrent event down at once. That exit had no log line at all."""

    async def abort(*_args: object, **_kwargs: object):
        raise RuntimeError("task group aborted")

    intervals = IntervalsConfiguration(
        user_cleanup=10,
        notify_start_meeting=20,
        notify_meeting_started=25,
        generate_stats=30,
        deactivate_meetings=40,
        meetups_cleanup=50,
        send_broadcasts=60,
        supporter_check=70,
    )

    with capture_logs() as logs:
        with patch("mitup_bot.events.service.run_periodic", side_effect=abort):
            with pytest.raises(BaseExceptionGroup):
                await run_all_tasks(intervals, MagicMock(), MagicMock(), [], start_time=0.0)

    stopped = next(log for log in logs if log["event"] == "Recurrent event scheduling stopped")
    assert stopped["log_level"] == "error"
    assert stopped["reason"] == "task_group_aborted"


def make_intervals() -> IntervalsConfiguration:
    return IntervalsConfiguration(
        user_cleanup=10,
        notify_start_meeting=20,
        notify_meeting_started=25,
        generate_stats=30,
        deactivate_meetings=40,
        meetups_cleanup=50,
        send_broadcasts=60,
        supporter_check=70,
    )


@pytest.mark.parametrize(
    "stop_signal, expected_reason",
    [(signal.SIGTERM, "sigterm"), (signal.SIGINT, "sigint")],
    ids=["sigterm", "sigint"],
)
async def test_a_stop_signal_drains_every_job_and_returns_cleanly(stop_signal: signal.Signals, expected_reason: str):
    """ECS stops the container with SIGTERM. On the default disposition the process dies where it
    stands, so an in-flight job's `finally` never runs; the handler has to turn the signal into a
    task-group cancellation every job's teardown observes, then return instead of raising so the
    container exits 0."""
    started = asyncio.Event()
    drained: list[EventType] = []

    async def job_that_drains(
        _interval: int,
        event_type: EventType,
        bot: object,
        admin_tg_ids: list[int],
        time_before_start: float | None = None,
    ):
        started.set()
        try:
            await asyncio.sleep(3600)
        finally:
            drained.append(event_type)

    with capture_logs() as logs:
        with patch("mitup_bot.events.service.run_periodic", side_effect=job_that_drains):
            service = asyncio.create_task(run_all_tasks(make_intervals(), MagicMock(), MagicMock(), [], start_time=0.0))
            await started.wait()
            signal.raise_signal(stop_signal)
            # A drain that outlives this bound would outlive the ECS stop window too.
            await asyncio.wait_for(service, timeout=5)

    assert set(drained) == set(EventType)
    requested = next(log for log in logs if log["event"] == "Events shutdown requested")
    assert requested["reason"] == expected_reason
    assert requested["outcome"] == "cancelling"
    stopped = next(log for log in logs if log["event"] == "Recurrent event scheduling stopped")
    assert stopped["log_level"] == "info"
    assert stopped["reason"] == expected_reason
    finished = next(log for log in logs if log["event"] == "Events service stopped")
    assert finished["reason"] == expected_reason
    # The drain duration is what an operator compares against `stopTimeout`, so the line must carry it.
    assert "duration_ms" in finished


async def test_run_all_tasks_reports_a_cancellation_nobody_signalled():
    """A cancellation with no signal behind it is an abort, not a deploy: it keeps the error level
    and still propagates, so the container never exits 0 on a stop nobody asked for."""
    started = asyncio.Event()

    async def job_that_waits(*_args: object, **_kwargs: object):
        started.set()
        await asyncio.sleep(3600)

    with capture_logs() as logs:
        with patch("mitup_bot.events.service.run_periodic", side_effect=job_that_waits):
            service = asyncio.create_task(run_all_tasks(make_intervals(), MagicMock(), MagicMock(), [], start_time=0.0))
            await started.wait()
            service.cancel()
            with pytest.raises(CancelledError):
                await service

    stopped = next(log for log in logs if log["event"] == "Recurrent event scheduling stopped")
    assert stopped["log_level"] == "error"
    assert stopped["reason"] == "cancelled"
    finished = next(log for log in logs if log["event"] == "Events service stopped")
    assert finished["reason"] == "cancelled"


def test_a_repeated_stop_signal_escalates_without_restarting_the_drain_clock():
    """A second SIGTERM arriving mid-drain is an escalation of the shutdown already in flight.
    Re-stamping `requested_at` would understate every drain that needed a nudge, which is exactly
    the drain an operator is measuring against `stopTimeout`."""
    shutdown = ShutdownRequest()
    main_task = MagicMock()

    request_shutdown(shutdown, StopReason.SIGTERM, main_task)
    first_requested_at = shutdown.requested_at
    with capture_logs() as logs:
        request_shutdown(shutdown, StopReason.SIGTERM, main_task)

    assert shutdown.requested_at == first_requested_at
    assert main_task.cancel.call_count == 2
    requested = next(log for log in logs if log["event"] == "Events shutdown requested")
    assert requested["reason"] == "sigterm"
    assert requested["outcome"] == "already_stopping"


MAINTAINANCE_PARAMS = [
    (EventType.USER_CLEANUP, None, 0, 0, None),
    (EventType.GENERATE_STATS, RuntimeError("boom"), 0, 1, "RuntimeError"),
    (EventType.DEACTIVATE_MEETINGS, None, 3, 0, None),
]


@pytest.mark.parametrize(
    "event_type, launch_side_effect, leaked_connections, expected_fault, expected_exception",
    MAINTAINANCE_PARAMS,
    ids=["success", "fault", "leaked_connections"],
)
async def test_handle_maintainance(
    event_type: EventType,
    launch_side_effect: Exception | None,
    leaked_connections: int,
    expected_fault: int,
    expected_exception: str | None,
):
    captured_client: list[MetricsClient] = []
    fake_api = MagicMock()

    def make_client(backend, base_dimensions=None):
        client = make_test_metrics_client(base_dimensions=base_dimensions)
        captured_client.append(client)
        return client

    with (
        patch(
            "mitup_bot.events.service.dispatch_event",
            new_callable=AsyncMock,
            side_effect=launch_side_effect,
        ) as mock_dispatch,
        patch("mitup_bot.events.service.db") as mock_db,
        patch("mitup_bot.events.service.build_api", return_value=fake_api),
        patch("mitup_bot.events.service.MetricsClient", side_effect=make_client),
    ):
        mock_db.get_open_connections.return_value = leaked_connections

        with capture_logs() as logs:
            await handle_maintainance(event_type, MagicMock(), [])

        mock_db.set_connection_context.assert_called_once_with(event_type.value)
        assert len(captured_client) == 1
        client = captured_client[0]
        mock_dispatch.assert_awaited_once_with(event_type, fake_api, client, [])

        assertions = MetricAssertions(client)
        assertions.assert_emitted(
            name=MetricKey.FAULT,
            value=expected_fault,
            unit=MetricUnit.COUNT,
            dimensions={"EventType": event_type.value},
        )
        assertions.assert_emitted(
            name=MetricKey.TIME,
            unit=MetricUnit.MILLISECONDS,
            dimensions={"EventType": event_type.value},
        )
        # A leak belongs to the run, not to a series: the count rides the run's completion line,
        # including the zero that says the run returned every connection it took.
        finished = next(entry for entry in logs if entry["event"] == "Recurrent event run finished")
        assert finished["db_connections_leaked"] == leaked_connections

        # Fault and Time additionally emit a dimensionless global copy (no EventType dimension) so
        # a single Mitup/Events alarm can watch every event type; EventType survives as a property.
        assertions.assert_emitted(
            name=MetricKey.FAULT,
            value=expected_fault,
            unit=MetricUnit.COUNT,
            dimensions={},
            dimensions_exact=True,
            properties={"EventType": event_type.value},
        )
        assertions.assert_emitted(
            name=MetricKey.TIME,
            unit=MetricUnit.MILLISECONDS,
            dimensions={},
            dimensions_exact=True,
            properties={"EventType": event_type.value},
        )
        # Business stats stay EventType-dimensioned only — no global copy.
        assertions.assert_not_emitted(
            name=MetricKey.DB_CONNECTIONS_LEAKED,
            dimensions={},
            dimensions_exact=True,
        )


async def test_handle_maintainance_publishes_the_run_client_to_the_outbound_instrumentation():
    """The outbound instrumentation lives inside PTB's request object and the Patreon client,
    neither of which is reachable from the runner: it finds the run's client through the ambient
    binding, so its samples land in this run's flush window with its run_id."""
    captured_client: list[MetricsClient] = []
    ambient_seen: list[MetricsClient | None] = []

    def make_client(backend: MetricsBackend, base_dimensions: dict[str, str] | None = None) -> MetricsClient:
        assert not isinstance(backend, NullBackend), "Expected a real backend, not NullBackend"
        client = make_test_metrics_client(base_dimensions=base_dimensions)
        captured_client.append(client)
        return client

    async def record_ambient_client(
        event_type: EventType, api: TelegramApiWrapper, client: MetricsClient, admin_tg_ids: list[int]
    ):
        ambient_seen.append(current_metrics_client())

    with (
        patch("mitup_bot.events.service.dispatch_event", side_effect=record_ambient_client),
        patch("mitup_bot.events.service.db") as mock_db,
        patch("mitup_bot.events.service.MetricsClient", side_effect=make_client),
    ):
        mock_db.get_open_connections.return_value = 0

        await handle_maintainance(EventType.USER_CLEANUP, bot=AsyncMock(), admin_tg_ids=[])

    assert ambient_seen == captured_client
    # The binding is scoped to the run, so nothing leaks into the next one on this task.
    assert current_metrics_client() is None


class CapturingSink(Sink):
    """Sink that records every serialized EMF line, mirroring tests/monitoring/test_backend.py."""

    def __init__(self):
        self.serializer = LogSerializer()
        self.serialized: list[str] = []

    def accept(self, context: MetricsContext):
        self.serialized.extend(self.serializer.serialize(context))

    @staticmethod
    def name() -> str:
        return "CapturingSink"


class CapturingEnvironment(LocalEnvironment):
    def __init__(self, sink: CapturingSink):
        self.sink = sink


def build_capturing_backend() -> tuple[EmfBackend, CapturingSink]:
    sink = CapturingSink()
    environment = CapturingEnvironment(sink)

    async def resolver() -> Environment:
        return environment

    return EmfBackend(environment_provider=resolver), sink


def dimension_sets(payload: dict) -> list[set[str]]:
    return [set(dims) for directive in payload["_aws"]["CloudWatchMetrics"] for dims in directive["Dimensions"]]


def metric_names(payload: dict) -> set[str]:
    return {metric["Name"] for directive in payload["_aws"]["CloudWatchMetrics"] for metric in directive["Metrics"]}


@pytest.mark.parametrize(
    "launch_side_effect, expected_fault",
    [(None, 0), (RuntimeError("boom"), 1)],
    ids=["success", "fault"],
)
async def test_handle_maintainance_serializes_dimensioned_and_global_emf(
    launch_side_effect: Exception | None,
    expected_fault: int,
):
    """End-to-end through the real EmfBackend: every run serializes two EMF lines — the
    EventType-dimensioned Fault/Time series and a dimensionless global copy of them carrying
    EventType as a property (not a dimension) for cross-event alarming."""
    event_type = EventType.USER_CLEANUP
    backend, sink = build_capturing_backend()
    client = MetricsClient(backend, base_dimensions={"EventType": event_type.value})

    with (
        patch(
            "mitup_bot.events.service.dispatch_event",
            new_callable=AsyncMock,
            side_effect=launch_side_effect,
        ),
        patch("mitup_bot.events.service.db") as mock_db,
        patch("mitup_bot.events.service.build_api"),
    ):
        mock_db.get_open_connections.return_value = 0
        await handle_maintainance(event_type, MagicMock(), [], client=client)

    payloads = [json.loads(line) for line in sink.serialized]
    assert len(payloads) == 2

    dimensioned = next(p for p in payloads if {"EventType"} in dimension_sets(p))
    dimensionless = next(p for p in payloads if all(not dims for dims in dimension_sets(p)))

    # Dimensioned series: EventType is a real dimension, both metrics present.
    assert dimensioned["EventType"] == event_type.value
    assert dimensioned["Fault"] == expected_fault
    assert metric_names(dimensioned) == {str(MetricKey.FAULT), str(MetricKey.TIME)}

    # Global copy: only Fault/Time, EventType present as a property but not in any dimension set.
    assert metric_names(dimensionless) == {str(MetricKey.FAULT), str(MetricKey.TIME)}
    assert dimensionless["Fault"] == expected_fault
    assert dimensionless["EventType"] == event_type.value
    assert all("EventType" not in dims for dims in dimension_sets(dimensionless))

    # Every EMF record of the run carries the same run_id property, matching the run's structlog
    # contextvar so Fault records can be joined to their log lines.
    assert len(dimensioned["run_id"]) == 32
    assert dimensioned["run_id"] == dimensionless["run_id"]


async def test_handle_maintainance_fault_records_carry_no_traceback():
    """Neither the EventType-dimensioned Fault record nor its dimensionless global copy carries the
    traceback: the run's `Recurrent event run failed` log line renders it once, while a copy on the
    records is repeated per serialized batch and joins the log line by run_id anyway."""
    event_type = EventType.USER_CLEANUP
    backend, sink = build_capturing_backend()
    client = MetricsClient(backend, base_dimensions={"EventType": event_type.value})

    with (
        patch(
            "mitup_bot.events.service.dispatch_event",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ),
        patch("mitup_bot.events.service.db") as mock_db,
        patch("mitup_bot.events.service.build_api"),
    ):
        mock_db.get_open_connections.return_value = 0
        await handle_maintainance(event_type, MagicMock(), [], client=client)

    payloads = [json.loads(line) for line in sink.serialized]
    assert len(payloads) == 2

    for payload in payloads:
        assert payload["Fault"] == 1
        assert "exception" not in payload
        assert "boom" not in json.dumps(payload)


async def test_run_periodic_runs_event():
    bot = MagicMock()

    # First sleep is the time_before_start delay, second is the interval sleep after handle_maintainance
    with (
        patch("mitup_bot.events.service.asyncio.sleep", side_effect=[None, CancelledError()]),
        patch("mitup_bot.events.service.handle_maintainance", new_callable=AsyncMock) as mock_handle,
    ):
        with pytest.raises(CancelledError):
            await run_periodic(60, EventType.USER_CLEANUP, bot, [], time_before_start=0)

        mock_handle.assert_awaited_once_with(EventType.USER_CLEANUP, bot, [])


async def test_run_periodic_default_jitter():
    bot = MagicMock()
    sleep_values: list[float] = []

    async def sleep_side_effect(seconds: float):
        sleep_values.append(seconds)
        raise CancelledError

    with (
        patch("mitup_bot.events.service.asyncio.sleep", side_effect=sleep_side_effect),
        patch("mitup_bot.events.service.handle_maintainance", new_callable=AsyncMock),
    ):
        with pytest.raises(CancelledError):
            await run_periodic(100, EventType.USER_CLEANUP, bot, [], time_before_start=None)

    assert len(sleep_values) == 1
    # Jitter should be between 0 and 1% of the interval (100 * 0.01 = 1.0)
    assert 0 <= sleep_values[0] <= 1.0


async def test_run_all_tasks_creates_all_tasks():
    bot = MagicMock()
    broadcast_bot = MagicMock()
    intervals = IntervalsConfiguration(
        user_cleanup=10,
        notify_start_meeting=20,
        notify_meeting_started=25,
        generate_stats=30,
        deactivate_meetings=40,
        meetups_cleanup=50,
        send_broadcasts=60,
        supporter_check=60,
    )

    created_tasks: list[EventType] = []
    propagated_start_times: list[float | None] = []
    bots_by_event: dict[EventType, object] = {}
    start_time = 42.0

    async def fake_run_periodic(
        interval: int,
        event_type: EventType,
        bot: object,
        admin_tg_ids: list[int],
        time_before_start: float | None = None,
    ):
        created_tasks.append(event_type)
        propagated_start_times.append(time_before_start)
        bots_by_event[event_type] = bot

    with patch("mitup_bot.events.service.run_periodic", side_effect=fake_run_periodic):
        await run_all_tasks(intervals, bot, broadcast_bot, [], start_time=start_time)

    assert set(created_tasks) == set(EventType)
    assert propagated_start_times == [start_time] * len(EventType)
    # Only SEND_BROADCASTS runs on the rate-capped broadcast bot; every other event on the shared one.
    assert bots_by_event[EventType.SEND_BROADCASTS] is broadcast_bot
    assert all(bots_by_event[event] is bot for event in EventType if event is not EventType.SEND_BROADCASTS)


def test_cli_invokes_with_defaults():
    runner = CliRunner()

    with (
        patch("mitup_bot.events.service.load_config") as mock_load_config,
        patch("mitup_bot.events.service.db") as mock_db,
        patch("mitup_bot.events.service.configure_emf_backend") as mock_configure_emf,
        patch("mitup_bot.events.service.build_bot") as mock_build_bot,
        patch("mitup_bot.events.service.build_broadcast_bot") as mock_build_broadcast_bot,
        patch("mitup_bot.events.service.build_api"),
        patch("mitup_bot.events.service.asyncio.run") as mock_async_run,
    ):
        mock_config = MagicMock()
        mock_config.db.pool_metrics_enabled = False
        mock_config.patreon = create_patreon_config()
        mock_load_config.return_value = mock_config

        result = runner.invoke(cli, [])

        assert result.exit_code == 0, result.output
        mock_load_config.assert_called_once()
        # Flag off: the events pool stays uninstrumented — no metrics client passed to the db.
        mock_db.configure_db.assert_called_once_with(mock_config.db, metrics_client=None)
        mock_configure_emf.assert_called_once_with(mock_config.metrics)
        mock_build_broadcast_bot.assert_called_once_with(mock_config.bot)
        mock_build_bot.assert_called_once_with(mock_config.bot)
        mock_async_run.assert_called_once()


def test_cli_registers_the_outbox_reconciler():
    runner = CliRunner()

    with (
        patch("mitup_bot.events.service.load_config") as mock_load_config,
        patch("mitup_bot.events.service.db"),
        patch("mitup_bot.events.service.reconcile") as mock_reconcile,
        patch("mitup_bot.events.service.configure_emf_backend"),
        patch("mitup_bot.events.service.build_bot"),
        patch("mitup_bot.events.service.build_broadcast_bot"),
        patch("mitup_bot.events.service.build_api"),
        patch("mitup_bot.events.service.asyncio.run"),
    ):
        mock_config = MagicMock()
        mock_config.db.pool_metrics_enabled = False
        mock_config.patreon = create_patreon_config()
        mock_load_config.return_value = mock_config

        result = runner.invoke(cli, [])

        assert result.exit_code == 0, result.output
        # Write-mode lifecycles refuse to run without the reconciler: startup must wire it.
        mock_reconcile.register_outbox_reconciler.assert_called_once_with()


def test_cli_registers_the_update_guards():
    runner = CliRunner()

    with (
        patch("mitup_bot.events.service.load_config") as mock_load_config,
        patch("mitup_bot.events.service.db"),
        patch("mitup_bot.events.service.api_guards") as mock_api_guards,
        patch("mitup_bot.events.service.configure_emf_backend"),
        patch("mitup_bot.events.service.build_bot"),
        patch("mitup_bot.events.service.build_broadcast_bot"),
        patch("mitup_bot.events.service.build_api"),
        patch("mitup_bot.events.service.asyncio.run"),
    ):
        mock_config = MagicMock()
        mock_config.db.pool_metrics_enabled = False
        mock_config.patreon = create_patreon_config()
        mock_load_config.return_value = mock_config

        result = runner.invoke(cli, [])

        assert result.exit_code == 0, result.output
        # The api refuses to resolve a chat or query off an Update without the guards registered.
        mock_api_guards.register_update_guards.assert_called_once_with()


def test_cli_instruments_pool_when_pool_metrics_enabled():
    runner = CliRunner()

    with (
        patch("mitup_bot.events.service.load_config") as mock_load_config,
        patch("mitup_bot.events.service.db") as mock_db,
        patch("mitup_bot.events.service.configure_emf_backend"),
        patch("mitup_bot.events.service.build_bot"),
        patch("mitup_bot.events.service.build_broadcast_bot"),
        patch("mitup_bot.events.service.build_api"),
        patch("mitup_bot.events.service.asyncio.run"),
    ):
        mock_config = MagicMock()
        mock_config.db.pool_metrics_enabled = True
        mock_config.patreon = create_patreon_config()
        mock_load_config.return_value = mock_config

        result = runner.invoke(cli, [])

        assert result.exit_code == 0, result.output
        # Flag on: the events pool is instrumented with a metrics client.
        configure_call = mock_db.configure_db.call_args
        assert configure_call.args == (mock_config.db,)
        pool_client = configure_call.kwargs["metrics_client"]
        assert isinstance(pool_client, MetricsClient)
        # Two alarms page on the pool records, which carried no identity at all. The client outlives
        # every run, so the identity is set once at construction: `set_global_property` on a
        # process-lived client would pin a run-scoped value onto every later record. Reading the
        # backend's property bag is the only way to see what a not-yet-emitted record will carry.
        assert pool_client._backend._properties == {"component": "events"}  # noqa: SLF001


def test_cli_narrates_every_subsystem_it_wired():
    """The events container degrades quietly when Patreon or the hosts group is unconfigured, so
    what it wired — and with which resolved values — belongs at the top of its stream."""
    runner = CliRunner()

    with (
        patch("mitup_bot.events.service.load_config") as mock_load_config,
        patch("mitup_bot.events.service.configure_logging"),
        # The module logger rather than capture_logs: the sibling CLI tests run the real
        # configure_logging, which caches this module's bound logger past any later processor swap.
        patch("mitup_bot.events.service.log") as mock_log,
        patch("mitup_bot.events.service.db"),
        patch("mitup_bot.events.service.configure_emf_backend"),
        patch("mitup_bot.events.service.build_bot"),
        patch("mitup_bot.events.service.build_broadcast_bot"),
        patch("mitup_bot.events.service.build_api"),
        patch("mitup_bot.events.service.asyncio.run"),
    ):
        mock_config = MagicMock()
        mock_config.db.pool_metrics_enabled = False
        mock_config.patreon = create_patreon_config()
        mock_load_config.return_value = mock_config

        result = runner.invoke(cli, [])

    assert result.exit_code == 0, result.output
    assert [call.args[0] for call in mock_log.info.call_args_list] == [
        "Events service starting",
        "Configured the database",
        "Configured the metrics backend",
        "Configured the Patreon integration",
        "Configured the hosts-only group",
        "Built the events bots",
    ]


def test_cli_configures_logging_before_wiring_subsystems():
    """`configure_logging` runs ahead of the DB, reconciler, EMF and Patreon wiring.

    A failure while wiring any of them must reach the structured pipeline: emitted before the
    pipeline exists it matches no `component = "events"` query, leaving it invisible in exactly
    the situation an operator goes looking."""
    runner = CliRunner()
    call_order: list[str] = []

    with (
        patch("mitup_bot.events.service.load_config") as mock_load_config,
        patch(
            "mitup_bot.events.service.configure_logging", side_effect=lambda *_a, **_kw: call_order.append("logging")
        ),
        # The module logger rather than capture_logs: the sibling CLI tests run the real
        # configure_logging, which caches this module's bound logger past any later processor swap.
        patch("mitup_bot.events.service.log") as mock_log,
        patch("mitup_bot.events.service.db") as mock_db,
        patch("mitup_bot.events.service.configure_emf_backend", side_effect=lambda *_a: call_order.append("emf")),
        patch("mitup_bot.events.service.build_bot"),
        patch("mitup_bot.events.service.build_broadcast_bot"),
        patch("mitup_bot.events.service.build_api"),
        patch("mitup_bot.events.service.asyncio.run"),
    ):
        mock_db.configure_db.side_effect = lambda *_a, **_kw: call_order.append("db")
        mock_config = MagicMock()
        mock_config.db.pool_metrics_enabled = False
        mock_config.patreon = create_patreon_config()
        mock_load_config.return_value = mock_config

        result = runner.invoke(cli, [])

    assert result.exit_code == 0, result.output
    assert call_order == ["logging", "db", "emf"]
    starting = next(call for call in mock_log.info.call_args_list if call.args[0] == "Events service starting")
    # The schedule the container is actually running, readable without reaching for its task definition.
    assert starting.kwargs["intervals"][EventType.USER_CLEANUP.value] == DEFAULT_USER_CLEANUP_INTERVAL
    assert starting.kwargs["pool_metrics_enabled"] is False


def test_cli_passes_custom_intervals():
    runner = CliRunner()

    with (
        patch("mitup_bot.events.service.load_config") as mock_load_config,
        patch("mitup_bot.events.service.db"),
        patch("mitup_bot.events.service.configure_emf_backend"),
        patch("mitup_bot.events.service.build_bot"),
        patch("mitup_bot.events.service.build_broadcast_bot"),
        patch("mitup_bot.events.service.build_api"),
        patch("mitup_bot.events.service.run_all_tasks", new_callable=AsyncMock) as mock_run_all_tasks,
        patch("mitup_bot.events.service.asyncio.run") as mock_async_run,
    ):
        mock_config = MagicMock()
        mock_config.patreon = create_patreon_config()
        mock_load_config.return_value = mock_config

        result = runner.invoke(
            cli,
            [
                "--user-cleanup-interval",
                "111",
                "--notify-meeting-interval",
                "222",
                "--generate-stats-interval",
                "333",
                "--deactivate-meetings-interval",
                "444",
                "--meetups-cleanup-interval",
                "555",
                "--send-broadcasts-interval",
                "666",
                "--supporter-check-interval",
                "777",
                "--start-time",
                "1.5",
            ],
        )

        assert result.exit_code == 0, result.output
        mock_async_run.assert_called_once()

        # Verify the IntervalsConfiguration passed to run_all_tasks has the correct values
        mock_run_all_tasks.assert_called_once()
        intervals_arg, _bot, _broadcast_bot, _admin_tg_ids, start_time_arg = mock_run_all_tasks.call_args.args
        assert intervals_arg.user_cleanup == 111
        assert intervals_arg.notify_start_meeting == 222
        assert intervals_arg.generate_stats == 333
        assert intervals_arg.deactivate_meetings == 444
        assert intervals_arg.meetups_cleanup == 555
        assert intervals_arg.send_broadcasts == 666
        assert intervals_arg.supporter_check == 777
        assert start_time_arg == 1.5


@pytest.fixture(autouse=True)
def restore_patreon_state() -> Iterator[None]:
    """Save/restore the process-wide Patreon seams that ``configure_patreon`` mutates."""
    saved_config, saved_cipher = PatreonRuntime.config, TokenCipher.cipher
    try:
        yield
    finally:
        PatreonRuntime.config = saved_config
        TokenCipher.cipher = saved_cipher


def test_cli_configures_patreon():
    """The [patreon] section installs the token cipher and the runtime config for the process."""
    runner = CliRunner()
    patreon_config = create_patreon_config()

    with (
        patch("mitup_bot.events.service.load_config") as mock_load_config,
        patch("mitup_bot.events.service.db"),
        patch("mitup_bot.events.service.configure_emf_backend"),
        patch("mitup_bot.events.service.build_bot"),
        patch("mitup_bot.events.service.build_broadcast_bot"),
        patch("mitup_bot.events.service.build_api"),
        patch("mitup_bot.events.service.asyncio.run"),
    ):
        mock_config = MagicMock()
        mock_config.db.pool_metrics_enabled = False
        mock_config.patreon = patreon_config
        mock_load_config.return_value = mock_config

        result = runner.invoke(cli, [])

    assert result.exit_code == 0, result.output
    assert PatreonRuntime.config is patreon_config
    assert TokenCipher.cipher is not None


def test_cli_env_option():
    runner = CliRunner()

    with (
        patch("mitup_bot.events.service.load_config") as mock_load_config,
        patch("mitup_bot.events.service.db"),
        patch("mitup_bot.events.service.configure_emf_backend"),
        patch("mitup_bot.events.service.build_bot"),
        patch("mitup_bot.events.service.build_broadcast_bot"),
        patch("mitup_bot.events.service.build_api"),
        patch("mitup_bot.events.service.asyncio.run"),
    ):
        mock_config = MagicMock()
        mock_config.patreon = create_patreon_config()
        mock_load_config.return_value = mock_config

        result = runner.invoke(cli, ["--env", "prod"])

        assert result.exit_code == 0, result.output
        mock_load_config.assert_called_once()
