from asyncio import CancelledError
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from mitup_bot.cli.commands.recurrent_events import (
    EventType,
    IntervalsConfiguration,
    build_bot,
    cli,
    handle_maintainance,
    launch_event,
    run_all_tasks,
    run_periodic,
)
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit, NullBackend
from tests.helpers import MockApi
from tests.helpers.monitoring import MetricAssertions

INTERVAL_PARAMS = [
    (EventType.USER_CLEANUP, "user_cleanup"),
    (EventType.NOTIFY_START_MEETING, "notify_start_meeting"),
    (EventType.NOTIFY_MEETING_STARTED, "notify_meeting_started"),
    (EventType.GENERATE_STATS, "generate_stats"),
    (EventType.DEACTIVATE_MEETINGS, "deactivate_meetings"),
    (EventType.MEETUPS_CLEANUP, "meetups_cleanup"),
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
    )
    assert config.get(event_type) == getattr(config, field_name)


@patch("mitup_bot.cli.commands.recurrent_events.ExtBot")
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


# Async event types use `await module.run(...)`, sync ones call directly.
ASYNC_LAUNCH_PARAMS = [
    (EventType.NOTIFY_START_MEETING, "mitup_bot.cli.commands.recurrent_events.notify_meetings"),
    (EventType.NOTIFY_MEETING_STARTED, "mitup_bot.cli.commands.recurrent_events.notify_meetings_started"),
    (EventType.DEACTIVATE_MEETINGS, "mitup_bot.cli.commands.recurrent_events.inactive_meetings"),
    (EventType.MEETUPS_CLEANUP, "mitup_bot.cli.commands.recurrent_events.meetups_cleanup"),
]

SYNC_LAUNCH_PARAMS = [
    (EventType.USER_CLEANUP, "mitup_bot.cli.commands.recurrent_events.user_cleanup"),
    (EventType.GENERATE_STATS, "mitup_bot.cli.commands.recurrent_events.generate_stats"),
]


@pytest.mark.parametrize(
    "event_type, module_path",
    ASYNC_LAUNCH_PARAMS,
    ids=[e.name for e, _ in ASYNC_LAUNCH_PARAMS],
)
async def test_launch_event_async(event_type: EventType, module_path: str):
    api = MockApi()
    client = MetricsClient(NullBackend())

    with patch(f"{module_path}.run", new_callable=AsyncMock) as mock_run:
        await launch_event(event_type, api, client)
        mock_run.assert_awaited_once_with(api, client)


@pytest.mark.parametrize(
    "event_type, module_path",
    SYNC_LAUNCH_PARAMS,
    ids=[e.name for e, _ in SYNC_LAUNCH_PARAMS],
)
async def test_launch_event_sync(event_type: EventType, module_path: str):
    api = MockApi()
    client = MetricsClient(NullBackend())

    with patch(f"{module_path}.run") as mock_run:
        await launch_event(event_type, api, client)
        mock_run.assert_called_once_with(api, client)


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
        client = MetricsClient(NullBackend(), base_dimensions=base_dimensions)
        captured_client.append(client)
        return client

    with (
        patch(
            "mitup_bot.cli.commands.recurrent_events.launch_event",
            new_callable=AsyncMock,
            side_effect=launch_side_effect,
        ) as mock_launch,
        patch("mitup_bot.cli.commands.recurrent_events.db") as mock_db,
        patch("mitup_bot.cli.commands.recurrent_events.build_api", return_value=fake_api),
        patch("mitup_bot.cli.commands.recurrent_events.MetricsClient", side_effect=make_client),
    ):
        mock_db.get_open_connections.return_value = leaked_connections

        await handle_maintainance(event_type, MagicMock())

        mock_db.set_connection_context.assert_called_once_with(event_type.value)
        assert len(captured_client) == 1
        client = captured_client[0]
        mock_launch.assert_awaited_once_with(event_type, fake_api, client)

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


async def test_handle_maintainance_emits_telegram_api_time_metrics():
    """Verify TelegramApiTime metrics flow through the real MetricsClient via BotAdapter."""
    captured_client: list[MetricsClient] = []

    def make_client(backend, base_dimensions=None):
        assert not isinstance(backend, NullBackend), "Expected a real backend, not NullBackend"
        client = MetricsClient(NullBackend(), base_dimensions=base_dimensions)
        captured_client.append(client)
        return client

    bot = AsyncMock()

    async def trigger_api_call(event_type, api, client):
        """Simulate a Telegram API call inside an event to exercise with_time_metric."""
        await api.send_message_to_user(MagicMock(tg_user_id=123, lang="en"), "test")

    with (
        patch(
            "mitup_bot.cli.commands.recurrent_events.launch_event",
            side_effect=trigger_api_call,
        ),
        patch("mitup_bot.cli.commands.recurrent_events.db") as mock_db,
        patch("mitup_bot.cli.commands.recurrent_events.MetricsClient", side_effect=make_client),
    ):
        mock_db.get_open_connections.return_value = 0

        await handle_maintainance(EventType.USER_CLEANUP, bot)

        assert len(captured_client) == 1
        client = captured_client[0]
        assertions = MetricAssertions(client)

        # TelegramApiTime is emitted by BotAdapter.with_time_metric inside TelegramApi methods
        assertions.assert_emitted(
            name="TelegramApiTime",
            unit=MetricUnit.MILLISECONDS,
        )


async def test_run_periodic_runs_event():
    bot = MagicMock()

    # First sleep is the time_before_start delay, second is the interval sleep after handle_maintainance
    with (
        patch("mitup_bot.cli.commands.recurrent_events.asyncio.sleep", side_effect=[None, CancelledError()]),
        patch("mitup_bot.cli.commands.recurrent_events.handle_maintainance", new_callable=AsyncMock) as mock_handle,
    ):
        with pytest.raises(CancelledError):
            await run_periodic(60, EventType.USER_CLEANUP, bot, time_before_start=0)

        mock_handle.assert_awaited_once_with(EventType.USER_CLEANUP, bot)


async def test_run_periodic_default_jitter():
    bot = MagicMock()
    sleep_values: list[float] = []

    async def sleep_side_effect(seconds: float):
        sleep_values.append(seconds)
        raise CancelledError

    with (
        patch("mitup_bot.cli.commands.recurrent_events.asyncio.sleep", side_effect=sleep_side_effect),
        patch("mitup_bot.cli.commands.recurrent_events.handle_maintainance", new_callable=AsyncMock),
    ):
        with pytest.raises(CancelledError):
            await run_periodic(100, EventType.USER_CLEANUP, bot, time_before_start=None)

    assert len(sleep_values) == 1
    # Jitter should be between 0 and 1% of the interval (100 * 0.01 = 1.0)
    assert 0 <= sleep_values[0] <= 1.0


async def test_run_all_tasks_creates_all_tasks():
    bot = MagicMock()
    intervals = IntervalsConfiguration(
        user_cleanup=10,
        notify_start_meeting=20,
        notify_meeting_started=25,
        generate_stats=30,
        deactivate_meetings=40,
        meetups_cleanup=50,
    )

    created_tasks: list[EventType] = []
    propagated_start_times: list[float | None] = []
    start_time = 42.0

    async def fake_run_periodic(
        interval: int,
        event_type: EventType,
        bot,
        time_before_start: float | None = None,
    ):
        created_tasks.append(event_type)
        propagated_start_times.append(time_before_start)

    with patch("mitup_bot.cli.commands.recurrent_events.run_periodic", side_effect=fake_run_periodic):
        await run_all_tasks(intervals, bot, start_time=start_time)

    assert set(created_tasks) == set(EventType)
    assert propagated_start_times == [start_time] * len(EventType)


def test_cli_invokes_with_defaults():
    runner = CliRunner()

    with (
        patch("mitup_bot.cli.commands.recurrent_events.Config.from_providers") as mock_config_cls,
        patch("mitup_bot.cli.commands.recurrent_events.db") as mock_db,
        patch("mitup_bot.cli.commands.recurrent_events.configure_emf_backend") as mock_configure_emf,
        patch("mitup_bot.cli.commands.recurrent_events.build_bot") as mock_build_bot,
        patch("mitup_bot.cli.commands.recurrent_events.build_api"),
        patch("mitup_bot.cli.commands.recurrent_events.asyncio.run") as mock_async_run,
    ):
        mock_config = MagicMock()
        mock_config_cls.return_value = mock_config

        result = runner.invoke(cli, [])

        assert result.exit_code == 0, result.output
        mock_config_cls.assert_called_once()
        mock_db.configure_db.assert_called_once_with(mock_config.db)
        mock_configure_emf.assert_called_once_with(mock_config.metrics)
        mock_build_bot.assert_called_once_with(mock_config.bot)
        mock_async_run.assert_called_once()


def test_cli_passes_custom_intervals():
    runner = CliRunner()

    with (
        patch("mitup_bot.cli.commands.recurrent_events.Config.from_providers") as mock_config_cls,
        patch("mitup_bot.cli.commands.recurrent_events.db"),
        patch("mitup_bot.cli.commands.recurrent_events.configure_emf_backend"),
        patch("mitup_bot.cli.commands.recurrent_events.build_bot"),
        patch("mitup_bot.cli.commands.recurrent_events.build_api"),
        patch("mitup_bot.cli.commands.recurrent_events.run_all_tasks", new_callable=AsyncMock) as mock_run_all_tasks,
        patch("mitup_bot.cli.commands.recurrent_events.asyncio.run") as mock_async_run,
    ):
        mock_config = MagicMock()
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
                "--start-time",
                "1.5",
            ],
        )

        assert result.exit_code == 0, result.output
        mock_async_run.assert_called_once()

        # Verify the IntervalsConfiguration passed to run_all_tasks has the correct values
        mock_run_all_tasks.assert_called_once()
        intervals_arg, _, start_time_arg = mock_run_all_tasks.call_args.args
        assert intervals_arg.user_cleanup == 111
        assert intervals_arg.notify_start_meeting == 222
        assert intervals_arg.generate_stats == 333
        assert intervals_arg.deactivate_meetings == 444
        assert intervals_arg.meetups_cleanup == 555
        assert start_time_arg == 1.5


def test_cli_env_option():
    runner = CliRunner()

    with (
        patch("mitup_bot.cli.commands.recurrent_events.Config.from_providers") as mock_config_cls,
        patch("mitup_bot.cli.commands.recurrent_events.db"),
        patch("mitup_bot.cli.commands.recurrent_events.configure_emf_backend"),
        patch("mitup_bot.cli.commands.recurrent_events.build_bot"),
        patch("mitup_bot.cli.commands.recurrent_events.build_api"),
        patch("mitup_bot.cli.commands.recurrent_events.asyncio.run"),
    ):
        mock_config = MagicMock()
        mock_config_cls.return_value = mock_config

        result = runner.invoke(cli, ["--env", "prod"])

        assert result.exit_code == 0, result.output
        mock_config_cls.assert_called_once()
