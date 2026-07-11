import json
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
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from mitup_bot.events.service import (
    EventType,
    IntervalsConfiguration,
    build_bot,
    build_broadcast_bot,
    handle_maintainance,
    launch_event,
    run_all_tasks,
    run_periodic,
    select_bot,
)
from mitup_bot.events_cli import recurrent_events as cli
from mitup_bot.models.subscriptions import TokenCipher
from mitup_bot.monitoring import EmfBackend, MetricKey, MetricsClient, MetricUnit, NullBackend
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
    bot_config = MagicMock()
    bot_config.token.get_secret_value.return_value = "test-token"
    bot_config.retries_on_throttle = 5

    build_bot(bot_config)

    mock_ext_bot.assert_called_once()
    call_kwargs = mock_ext_bot.call_args.kwargs
    assert call_kwargs["token"] == "test-token"
    assert call_kwargs["rate_limiter"] is not None
    assert "defaults" not in call_kwargs


@patch("mitup_bot.events.service.AIORateLimiter")
@patch("mitup_bot.events.service.ExtBot")
def test_build_broadcast_bot_applies_configured_rate(mock_ext_bot: MagicMock, mock_rate_limiter: MagicMock):
    bot_config = MagicMock()
    bot_config.token.get_secret_value.return_value = "test-token"
    bot_config.retries_on_throttle = 3
    bot_config.broadcast_max_rate = 7

    build_broadcast_bot(bot_config)

    # The broadcast bot caps its send rate at broadcast_max_rate per second while sharing the
    # retry budget with the main bot.
    mock_rate_limiter.assert_called_once_with(overall_max_rate=7, overall_time_period=1, max_retries=3)
    mock_ext_bot.assert_called_once()
    call_kwargs = mock_ext_bot.call_args.kwargs
    assert call_kwargs["token"] == "test-token"
    assert call_kwargs["rate_limiter"] is mock_rate_limiter.return_value


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
async def test_launch_event_async(event_type: EventType, module_path: str):
    api = MockApi()
    client = make_test_metrics_client()

    with patch(f"{module_path}.run", new_callable=AsyncMock) as mock_run:
        await launch_event(event_type, api, client, [])
        mock_run.assert_awaited_once_with(api, client)


async def test_launch_event_send_broadcasts():
    """SEND_BROADCASTS dispatches send_broadcasts.run with the operator allowlist appended."""
    api = MockApi()
    client = make_test_metrics_client()
    admin_tg_ids = [111, 222]

    with patch("mitup_bot.events.service.broadcast.run", new_callable=AsyncMock) as mock_run:
        await launch_event(EventType.SEND_BROADCASTS, api, client, admin_tg_ids)
        mock_run.assert_awaited_once_with(api, client, admin_tg_ids)


@pytest.mark.parametrize(
    "event_type, module_path",
    SYNC_LAUNCH_PARAMS,
    ids=[e.name for e, _ in SYNC_LAUNCH_PARAMS],
)
async def test_launch_event_sync(event_type: EventType, module_path: str):
    api = MockApi()
    client = make_test_metrics_client()

    with patch(f"{module_path}.run") as mock_run:
        await launch_event(event_type, api, client, [])
        mock_run.assert_called_once_with(api, client)


async def test_launch_event_binds_event_contextvars():
    """A log emitted while an event runs carries flow/event_type (both the dispatched EventType.value)
    and a run_id, bound by launch_event for the duration of the dispatched run()."""
    api = MockApi()
    client = make_test_metrics_client()

    def run_emitting_log(_api: object, _client: object):
        structlog.get_logger("mitup_bot").info("event running")

    with capture_logs(processors=[merge_contextvars]) as logs:
        with patch("mitup_bot.events.service.user_cleanup.run", side_effect=run_emitting_log):
            await launch_event(EventType.USER_CLEANUP, api, client, [])

    event_logs = [log for log in logs if log["event"] == "event running"]
    assert len(event_logs) == 1
    entry = event_logs[0]
    assert entry["flow"] == EventType.USER_CLEANUP.value  # "UserCleanup"
    assert entry["event_type"] == EventType.USER_CLEANUP.value  # "UserCleanup"
    # run_id is a uuid4().hex — present and a 32-char hex string, but its exact value is random.
    run_id = entry["run_id"]
    assert isinstance(run_id, str)
    assert len(run_id) == 32  # uuid4().hex


async def test_launch_event_clears_contextvars_between_events():
    """bound_contextvars auto-clears on exit, so flow/event_type/run_id must not leak from one event
    into a log emitted after launch_event returns (events run back-to-back in run_periodic)."""
    api = MockApi()
    client = make_test_metrics_client()

    with capture_logs(processors=[merge_contextvars]) as logs:
        with patch("mitup_bot.events.service.user_cleanup.run"):
            await launch_event(EventType.USER_CLEANUP, api, client, [])
        # Emitted after launch_event returned — must carry neither event field.
        structlog.get_logger("mitup_bot").info("between events")

    after_logs = [log for log in logs if log["event"] == "between events"]
    assert len(after_logs) == 1
    entry = after_logs[0]
    assert "flow" not in entry
    assert "event_type" not in entry
    assert "run_id" not in entry


async def test_launch_event_uses_distinct_run_id_per_invocation():
    """Each launch_event invocation generates a fresh run_id so back-to-back events are distinguishable."""
    api = MockApi()
    client = make_test_metrics_client()

    def capture_run_id(_api: object, _client: object):
        structlog.get_logger("mitup_bot").info("event running")

    with capture_logs(processors=[merge_contextvars]) as logs:
        with patch("mitup_bot.events.service.user_cleanup.run", side_effect=capture_run_id):
            await launch_event(EventType.USER_CLEANUP, api, client, [])
            await launch_event(EventType.USER_CLEANUP, api, client, [])

    run_ids = [log["run_id"] for log in logs if log["event"] == "event running"]
    assert len(run_ids) == 2
    assert run_ids[0] != run_ids[1]


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
            "mitup_bot.events.service.launch_event",
            new_callable=AsyncMock,
            side_effect=launch_side_effect,
        ) as mock_launch,
        patch("mitup_bot.events.service.db") as mock_db,
        patch("mitup_bot.events.service.build_api", return_value=fake_api),
        patch("mitup_bot.events.service.MetricsClient", side_effect=make_client),
    ):
        mock_db.get_open_connections.return_value = leaked_connections

        await handle_maintainance(event_type, MagicMock(), [])

        mock_db.set_connection_context.assert_called_once_with(event_type.value)
        assert len(captured_client) == 1
        client = captured_client[0]
        mock_launch.assert_awaited_once_with(event_type, fake_api, client, [])

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
        assertions.assert_emitted(
            name=MetricKey.DB_CONNECTIONS_LEAKED,
            value=leaked_connections,
            unit=MetricUnit.COUNT,
            dimensions={"EventType": event_type.value},
        )

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
        # DbConnectionsLeaked and any business stats stay EventType-dimensioned only — no global copy.
        assertions.assert_not_emitted(
            name=MetricKey.DB_CONNECTIONS_LEAKED,
            dimensions={},
            dimensions_exact=True,
        )


async def test_handle_maintainance_emits_telegram_api_time_metrics():
    """Verify TelegramApiTime metrics flow through the real MetricsClient via BotAdapter."""
    captured_client: list[MetricsClient] = []

    def make_client(backend, base_dimensions=None):
        assert not isinstance(backend, NullBackend), "Expected a real backend, not NullBackend"
        client = make_test_metrics_client(base_dimensions=base_dimensions)
        captured_client.append(client)
        return client

    bot = AsyncMock()

    async def trigger_api_call(event_type, api, client, admin_tg_ids):
        """Simulate a Telegram API call inside an event to exercise with_time_metric."""
        await api.send_message_to_user(MagicMock(tg_user_id=123, lang="en"), "test")

    with (
        patch(
            "mitup_bot.events.service.launch_event",
            side_effect=trigger_api_call,
        ),
        patch("mitup_bot.events.service.db") as mock_db,
        patch("mitup_bot.events.service.MetricsClient", side_effect=make_client),
    ):
        mock_db.get_open_connections.return_value = 0

        await handle_maintainance(EventType.USER_CLEANUP, bot, [])

        assert len(captured_client) == 1
        client = captured_client[0]
        assertions = MetricAssertions(client)

        # TelegramApiTime is emitted by BotAdapter.with_time_metric inside TelegramApi methods
        assertions.assert_emitted(
            name="TelegramApiTime",
            unit=MetricUnit.MILLISECONDS,
        )


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
    EventType-dimensioned series (Fault/Time/DbConnectionsLeaked) and a dimensionless global copy
    of Fault/Time carrying EventType as a property (not a dimension) for cross-event alarming."""
    event_type = EventType.USER_CLEANUP
    backend, sink = build_capturing_backend()
    client = MetricsClient(backend, base_dimensions={"EventType": event_type.value})

    with (
        patch(
            "mitup_bot.events.service.launch_event",
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

    # Dimensioned series: EventType is a real dimension, all three metrics present.
    assert dimensioned["EventType"] == event_type.value
    assert dimensioned["Fault"] == expected_fault
    assert metric_names(dimensioned) == {
        str(MetricKey.FAULT),
        str(MetricKey.TIME),
        str(MetricKey.DB_CONNECTIONS_LEAKED),
    }

    # Global copy: only Fault/Time, EventType present as a property but not in any dimension set.
    assert metric_names(dimensionless) == {str(MetricKey.FAULT), str(MetricKey.TIME)}
    assert dimensionless["Fault"] == expected_fault
    assert dimensionless["EventType"] == event_type.value
    assert all("EventType" not in dims for dims in dimension_sets(dimensionless))


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
        patch("mitup_bot.events.service.Config.from_providers") as mock_config_cls,
        patch("mitup_bot.events.service.db") as mock_db,
        patch("mitup_bot.events.service.configure_emf_backend") as mock_configure_emf,
        patch("mitup_bot.events.service.build_bot") as mock_build_bot,
        patch("mitup_bot.events.service.build_broadcast_bot") as mock_build_broadcast_bot,
        patch("mitup_bot.events.service.build_api"),
        patch("mitup_bot.events.service.asyncio.run") as mock_async_run,
    ):
        mock_config = MagicMock()
        mock_config.db.pool_metrics_enabled = False
        mock_config.patreon = None
        mock_config_cls.return_value = mock_config

        result = runner.invoke(cli, [])

        assert result.exit_code == 0, result.output
        mock_config_cls.assert_called_once()
        # Flag off: the events pool stays uninstrumented — no metrics client passed to the db.
        mock_db.configure_db.assert_called_once_with(mock_config.db, metrics_client=None)
        mock_configure_emf.assert_called_once_with(mock_config.metrics)
        mock_build_broadcast_bot.assert_called_once_with(mock_config.bot)
        mock_build_bot.assert_called_once_with(mock_config.bot)
        mock_async_run.assert_called_once()


def test_cli_registers_the_outbox_reconciler():
    runner = CliRunner()

    with (
        patch("mitup_bot.events.service.Config.from_providers") as mock_config_cls,
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
        mock_config.patreon = None
        mock_config_cls.return_value = mock_config

        result = runner.invoke(cli, [])

        assert result.exit_code == 0, result.output
        # Write-mode lifecycles refuse to run without the reconciler: startup must wire it.
        mock_reconcile.register_outbox_reconciler.assert_called_once_with()


def test_cli_registers_the_update_guards():
    runner = CliRunner()

    with (
        patch("mitup_bot.events.service.Config.from_providers") as mock_config_cls,
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
        mock_config.patreon = None
        mock_config_cls.return_value = mock_config

        result = runner.invoke(cli, [])

        assert result.exit_code == 0, result.output
        # The api refuses to resolve a chat or query off an Update without the guards registered.
        mock_api_guards.register_update_guards.assert_called_once_with()


def test_cli_instruments_pool_when_pool_metrics_enabled():
    runner = CliRunner()

    with (
        patch("mitup_bot.events.service.Config.from_providers") as mock_config_cls,
        patch("mitup_bot.events.service.db") as mock_db,
        patch("mitup_bot.events.service.configure_emf_backend"),
        patch("mitup_bot.events.service.build_bot"),
        patch("mitup_bot.events.service.build_broadcast_bot"),
        patch("mitup_bot.events.service.build_api"),
        patch("mitup_bot.events.service.asyncio.run"),
    ):
        mock_config = MagicMock()
        mock_config.db.pool_metrics_enabled = True
        mock_config.patreon = None
        mock_config_cls.return_value = mock_config

        result = runner.invoke(cli, [])

        assert result.exit_code == 0, result.output
        # Flag on: the events pool is instrumented with a metrics client.
        configure_call = mock_db.configure_db.call_args
        assert configure_call.args == (mock_config.db,)
        assert isinstance(configure_call.kwargs["metrics_client"], MetricsClient)


def test_cli_passes_custom_intervals():
    runner = CliRunner()

    with (
        patch("mitup_bot.events.service.Config.from_providers") as mock_config_cls,
        patch("mitup_bot.events.service.db"),
        patch("mitup_bot.events.service.configure_emf_backend"),
        patch("mitup_bot.events.service.build_bot"),
        patch("mitup_bot.events.service.build_broadcast_bot"),
        patch("mitup_bot.events.service.build_api"),
        patch("mitup_bot.events.service.run_all_tasks", new_callable=AsyncMock) as mock_run_all_tasks,
        patch("mitup_bot.events.service.asyncio.run") as mock_async_run,
    ):
        mock_config = MagicMock()
        mock_config.patreon = None
        mock_config_cls.return_value = mock_config

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


@pytest.fixture
def restore_patreon_state() -> Iterator[None]:
    """Save/restore the process-wide Patreon seams that ``configure_patreon`` mutates."""
    saved_config, saved_cipher = PatreonRuntime.config, TokenCipher.cipher
    try:
        yield
    finally:
        PatreonRuntime.config = saved_config
        TokenCipher.cipher = saved_cipher


def test_cli_configures_patreon_when_section_present(restore_patreon_state: None):
    """A present [patreon] section installs the token cipher and the runtime config for the process.

    Driven through the CLI (the no-section path is already exercised by the other cli tests, which
    all set ``patreon = None``)."""
    runner = CliRunner()
    patreon_config = create_patreon_config()

    with (
        patch("mitup_bot.events.service.Config.from_providers") as mock_config_cls,
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
        mock_config_cls.return_value = mock_config

        result = runner.invoke(cli, [])

    assert result.exit_code == 0, result.output
    assert PatreonRuntime.config is patreon_config
    assert TokenCipher.cipher is not None


def test_cli_env_option():
    runner = CliRunner()

    with (
        patch("mitup_bot.events.service.Config.from_providers") as mock_config_cls,
        patch("mitup_bot.events.service.db"),
        patch("mitup_bot.events.service.configure_emf_backend"),
        patch("mitup_bot.events.service.build_bot"),
        patch("mitup_bot.events.service.build_broadcast_bot"),
        patch("mitup_bot.events.service.build_api"),
        patch("mitup_bot.events.service.asyncio.run"),
    ):
        mock_config = MagicMock()
        mock_config.patreon = None
        mock_config_cls.return_value = mock_config

        result = runner.invoke(cli, ["--env", "prod"])

        assert result.exit_code == 0, result.output
        mock_config_cls.assert_called_once()
