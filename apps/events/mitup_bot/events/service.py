import asyncio
import signal
from dataclasses import dataclass
from enum import Enum, StrEnum
from functools import partial
from random import uniform
from time import perf_counter
from typing import assert_never
from uuid import uuid4

import structlog
from telegram.ext import AIORateLimiter, ExtBot

from mitup_bot import api_guards, db, hosts_group, patreon, reconcile
from mitup_bot.api_wrapper import BotAdapter, TelegramApiWrapper, build_api
from mitup_bot.bootstrap import load_config
from mitup_bot.config import BotConfig, Config, Env
from mitup_bot.events import (
    broadcast,
    generate_stats,
    inactive_meetings,
    meetups_cleanup,
    notify_meetings,
    notify_meetings_started,
    supporter_check,
    user_cleanup,
)
from mitup_bot.logging_config import Component, configure_logging
from mitup_bot.models import configure_token_encryption
from mitup_bot.monitoring import (
    EmfBackend,
    MetricKey,
    MetricsClient,
    MetricUnit,
    bound_metrics_client,
    configure_emf_backend,
)
from mitup_bot.request import build_telegram_request

from .lifecycle_queries import loggable_windows
from .telemetry import error_type_name

log = structlog.get_logger(__name__)

DEFAULT_USER_CLEANUP_INTERVAL = 3600
DEFAULT_GENERATE_STATS_INTERVAL = 21600  # 6 hours
DEFAULT_NOTIFY_MEETINGS_START = 60
DEFAULT_DEACTIVATE_MEETINGS_INTERVAL = 60
DEFAULT_MEETUPS_CLEANUP_INTERVAL = 86400  # 24 hours
DEFAULT_NOTIFY_MEETING_STARTED_INTERVAL = 60
DEFAULT_SEND_BROADCASTS_INTERVAL = 60
DEFAULT_SUPPORTER_CHECK_INTERVAL = 86400  # 24 hours


def lifecycle_windows() -> dict[str, dict[str, int]]:
    """The retention windows this process enforces, per duration and supporter level, in days.

    Every lifecycle statement bakes its intervals in at import time, so without this a policy edit
    ships with the image tag as its only evidence and the running container cannot be asked what it
    is enforcing.
    """
    return {
        "dateless_lifetime": loggable_windows(lambda policy: policy.dateless_lifetime),
        "inactive_retention": loggable_windows(lambda policy: policy.inactive_retention),
        "deletion_warning_delay": loggable_windows(lambda policy: policy.deletion_warning_delay),
    }


class EventType(Enum):
    USER_CLEANUP = "UserCleanup"
    GENERATE_STATS = "Stats"
    NOTIFY_START_MEETING = "NotifyStartMeeting"
    NOTIFY_MEETING_STARTED = "NotifyMeetingStarted"
    DEACTIVATE_MEETINGS = "DeactivateMeetings"
    MEETUPS_CLEANUP = "MeetupsCleanup"
    SEND_BROADCASTS = "SendBroadcasts"
    SUPPORTER_CHECK = "SupporterCheck"


@dataclass
class IntervalsConfiguration:
    user_cleanup: int
    notify_start_meeting: int
    notify_meeting_started: int
    generate_stats: int
    deactivate_meetings: int
    meetups_cleanup: int
    send_broadcasts: int
    supporter_check: int

    def get(self, event_type: EventType) -> int:
        match event_type:
            case EventType.USER_CLEANUP:
                return self.user_cleanup
            case EventType.NOTIFY_START_MEETING:
                return self.notify_start_meeting
            case EventType.NOTIFY_MEETING_STARTED:
                return self.notify_meeting_started
            case EventType.GENERATE_STATS:
                return self.generate_stats
            case EventType.DEACTIVATE_MEETINGS:
                return self.deactivate_meetings
            case EventType.MEETUPS_CLEANUP:
                return self.meetups_cleanup
            case EventType.SEND_BROADCASTS:
                return self.send_broadcasts
            case EventType.SUPPORTER_CHECK:
                return self.supporter_check
            case never:  # pragma: no cover
                assert_never(never)  # pragma: no cover


def configure_patreon(config: Config):
    """Wire the token cipher and Patreon runtime for the events process, so the SUPPORTER_CHECK
    job can decrypt/encrypt tokens and read the live config."""
    configure_token_encryption(*config.patreon.encryption_keys())
    patreon.configure(config.patreon)


def build_bot(config: BotConfig) -> ExtBot:
    return ExtBot(
        token=config.token.get_secret_value(),
        rate_limiter=AIORateLimiter(max_retries=config.retries_on_throttle),
        request=build_telegram_request(config),
    )


def build_broadcast_bot(config: BotConfig) -> ExtBot:
    """A separate bot instance for broadcast fan-out with a low proactive per-second cap.

    Broadcasts are the highest-rate, least time-sensitive traffic; giving them their own limiter
    keeps their volume from competing with time-sensitive events (meeting reminders) for the shared
    bot's rate budget. `AIORateLimiter` needs no lifecycle setup — its limiters are built in
    `__init__` and its `initialize`/`shutdown` are no-ops.
    """
    return ExtBot(
        token=config.token.get_secret_value(),
        rate_limiter=AIORateLimiter(
            overall_max_rate=config.broadcast_max_rate,
            overall_time_period=1,
            max_retries=config.retries_on_throttle,
        ),
        request=build_telegram_request(config),
    )


def select_bot(event_type: EventType, bot: ExtBot, broadcast_bot: ExtBot) -> ExtBot:
    """SEND_BROADCASTS runs on the rate-capped broadcast bot; every other event on the shared one."""
    return broadcast_bot if event_type is EventType.SEND_BROADCASTS else bot


async def dispatch_event(
    event_type: EventType, api: TelegramApiWrapper, client: MetricsClient, admin_tg_ids: list[int]
):
    match event_type:
        case EventType.USER_CLEANUP:
            await user_cleanup.run(api, client)
        case EventType.NOTIFY_START_MEETING:
            await notify_meetings.run(api, client)
        case EventType.NOTIFY_MEETING_STARTED:
            await notify_meetings_started.run(api, client)
        case EventType.GENERATE_STATS:
            await generate_stats.run(api, client)
        case EventType.DEACTIVATE_MEETINGS:
            await inactive_meetings.run(api, client)
        case EventType.MEETUPS_CLEANUP:
            await meetups_cleanup.run(api, client)
        case EventType.SEND_BROADCASTS:
            await broadcast.run(api, client, admin_tg_ids)
        case EventType.SUPPORTER_CHECK:
            await supporter_check.run(api, client)
        case never:  # pragma: no cover
            assert_never(never)  # pragma: no cover


async def handle_maintainance(
    event_type: EventType, bot: ExtBot, admin_tg_ids: list[int], client: MetricsClient | None = None
):
    client = client or MetricsClient(EmfBackend(), base_dimensions={"EventType": event_type.value})
    api = build_api(BotAdapter(bot, client))

    # run_id rides on every EMF record of this run as a global property, so a Fault record in
    # CloudWatch joins the structlog lines that carry the same run_id via contextvars.
    run_id = uuid4().hex
    client.set_global_property("run_id", run_id)

    start_time = perf_counter()
    fault = False
    outcome = "completed"
    # `flow` names the business unit across every service in the log plane, so one query reads the
    # bot and events lines together; the metric plane carries the same value as the EventType
    # dimension. A second log key holding it would be redundant on every line the plane emits.
    # Publishing the run's client makes the outbound-call instrumentation — inside PTB's request
    # object and inside the Patreon client, neither of which is reachable from here — emit into
    # this run's flush window, so its samples inherit the run_id already on these records.
    with (
        structlog.contextvars.bound_contextvars(flow=event_type.value, run_id=run_id),
        bound_metrics_client(client),
    ):
        # The start line is what makes run_id filterable at all: without it an empty result for a
        # run_id is ambiguous between "never ran" and "ran and decided nothing".
        log.info("Recurrent event run started")
        try:
            db.set_connection_context(event_type.value)
            await dispatch_event(event_type, api, client, admin_tg_ids)
        except asyncio.CancelledError:
            # A shutdown cancels the run where it stands: it neither completed nor faulted, and
            # recording it either way would misreport every job a rolling deploy interrupts.
            outcome = "interrupted"
            raise
        except Exception as error:
            fault = True
            outcome = "failed"
            client.emit(MetricKey.FAULT, 1, MetricUnit.COUNT, emit_global=True)
            log.exception(
                "Recurrent event run failed",
                outcome=outcome,
                error_type=error_type_name(error),
                duration_ms=round((perf_counter() - start_time) * 1000),
            )
        finally:
            # emit_global adds a dimensionless copy of Fault/Time so a single Mitup/Events alarm can
            # watch "any event type is failing"/aggregate run duration; EventType stays as an EMF
            # property on those copies for per-event breakdown in Logs Insights.
            if not fault:
                client.emit(MetricKey.FAULT, 0, MetricUnit.COUNT, emit_global=True)
            client.emit(MetricKey.TIME, (perf_counter() - start_time) * 1000, MetricUnit.MILLISECONDS, emit_global=True)
            leaked_connections = db.get_open_connections(event_type.value)
            log.info(
                "Recurrent event run finished",
                outcome=outcome,
                duration_ms=round((perf_counter() - start_time) * 1000),
                db_connections_leaked=leaked_connections,
            )
            await client.flush()


async def run_periodic(
    interval: int,
    event_type: EventType,
    bot: ExtBot,
    admin_tg_ids: list[int],
    time_before_start: float | None = None,
):
    # If no time provided add 1% interval jitter
    time_before_start = time_before_start if time_before_start is not None else uniform(0, interval * 0.01)
    await asyncio.sleep(time_before_start)

    # Run the coroutine indefinitely
    while True:
        await handle_maintainance(event_type, bot, admin_tg_ids)
        await asyncio.sleep(interval)


class StopReason(StrEnum):
    SIGTERM = "sigterm"
    SIGINT = "sigint"
    CANCELLED = "cancelled"
    TASK_GROUP_ABORTED = "task_group_aborted"
    SCHEDULING_ENDED = "scheduling_ended"


SHUTDOWN_SIGNALS = {
    signal.SIGTERM: StopReason.SIGTERM,
    signal.SIGINT: StopReason.SIGINT,
}


@dataclass
class ShutdownRequest:
    """Which signal asked this process to stop, and when it landed.

    A cancelled task group on its own cannot say whether an orchestrator asked us to stop or the
    process is aborting, and the two need a different level, a different reason and a different
    exit code.
    """

    reason: StopReason | None = None
    requested_at: float = 0.0

    def drain_ms(self) -> int:
        """Milliseconds between the signal landing and the teardown finishing, or 0 if no signal
        arrived. This is the number the ECS `stopTimeout` is compared against."""
        return round((perf_counter() - self.requested_at) * 1000) if self.reason else 0


def request_shutdown(shutdown: ShutdownRequest, reason: StopReason, main_task: asyncio.Task):
    """Cancel the task owning the event task group so every job's `finally` runs before exit."""
    repeated = shutdown.reason is not None
    if not repeated:
        shutdown.requested_at = perf_counter()
    shutdown.reason = reason
    log.info(
        "Events shutdown requested",
        reason=reason.value,
        outcome="already_stopping" if repeated else "cancelling",
    )
    main_task.cancel()


def install_shutdown_handlers(shutdown: ShutdownRequest, main_task: asyncio.Task):
    loop = asyncio.get_running_loop()
    for stop_signal, reason in SHUTDOWN_SIGNALS.items():
        loop.add_signal_handler(stop_signal, partial(request_shutdown, shutdown, reason, main_task))


def remove_shutdown_handlers():
    """Drop the handlers before draining: past this point a repeated signal must not interrupt the
    teardown it is waiting for, and SIGKILL remains the escape hatch."""
    loop = asyncio.get_running_loop()
    for stop_signal in SHUTDOWN_SIGNALS:
        loop.remove_signal_handler(stop_signal)


def log_scheduling_aborted(reason: StopReason, error: BaseException):
    """`handle_maintainance` swallows Exception, so the loops can only stop on a BaseException that
    takes every recurrent event down at once — the one failure mode with no per-run trace."""
    log.error(
        "Recurrent event scheduling stopped",
        reason=reason.value,
        error_type=error_type_name(error),
        exc_info=error,
    )


async def schedule_recurrent_events(
    intervals: IntervalsConfiguration,
    bot: ExtBot,
    broadcast_bot: ExtBot,
    admin_tg_ids: list[int],
    start_time: float,
):
    async with asyncio.TaskGroup() as task_group:
        for event_type in EventType:
            selected_bot = select_bot(event_type, bot, broadcast_bot)
            log.info(
                "Registered recurrent event",
                flow=event_type.value,
                interval_seconds=intervals.get(event_type),
                bot="broadcast" if selected_bot is broadcast_bot else "shared",
            )
            task_group.create_task(
                run_periodic(
                    intervals.get(event_type),
                    time_before_start=start_time,
                    event_type=event_type,
                    bot=selected_bot,
                    admin_tg_ids=admin_tg_ids,
                )
            )


async def run_all_tasks(
    intervals: IntervalsConfiguration,
    bot: ExtBot,
    broadcast_bot: ExtBot,
    admin_tg_ids: list[int],
    start_time: float,
):
    """Schedule every recurrent event and drain cleanly when a signal or an abort stops the group."""
    main_task = asyncio.current_task()
    assert main_task is not None, "asyncio.run always drives this coroutine inside a task"
    shutdown = ShutdownRequest()
    install_shutdown_handlers(shutdown, main_task)
    stop_reason = StopReason.SCHEDULING_ENDED
    try:
        await schedule_recurrent_events(intervals, bot, broadcast_bot, admin_tg_ids, start_time)
    except asyncio.CancelledError as error:
        if shutdown.reason is None:
            stop_reason = StopReason.CANCELLED
            log_scheduling_aborted(stop_reason, error)
            raise
        stop_reason = shutdown.reason
        # Balancing the cancellation we swallow keeps `cancelling()` truthful for any await the
        # teardown below still has to make, and lets the process exit 0 instead of on a traceback.
        main_task.uncancel()
        log.info("Recurrent event scheduling stopped", reason=stop_reason.value)
    except BaseException as error:
        stop_reason = StopReason.TASK_GROUP_ABORTED
        log_scheduling_aborted(stop_reason, error)
        raise
    finally:
        remove_shutdown_handlers()
        log.info("Events service stopped", reason=stop_reason.value, duration_ms=shutdown.drain_ms())


def run_events(env: Env, intervals: IntervalsConfiguration, start_time: float):
    """Bootstrap the events process and run every recurrent job on its interval until cancelled."""
    config = load_config(env, Component.EVENTS)

    # Ahead of every subsystem below, so a failure while wiring the DB, the reconciler or Patreon is
    # emitted through the structured pipeline instead of unstructured to stderr.
    configure_logging(env, Component.EVENTS, config.app.log_level, config.app.release)
    log.info(
        "Events service starting",
        env=env.value,
        log_level=config.app.log_level,
        intervals={event_type.value: intervals.get(event_type) for event_type in EventType},
        admin_count=len(config.bot.admin_tg_ids),
        pool_metrics_enabled=config.db.pool_metrics_enabled,
        broadcast_max_rate=config.bot.broadcast_max_rate,
        lifecycle_windows=lifecycle_windows(),
    )

    # The pool client outlives every run, so it carries the process identity and nothing else: a
    # run-scoped global property would stick to every record emitted after that run ended.
    pool_metrics_client = (
        MetricsClient(EmfBackend(properties={"component": Component.EVENTS.value}))
        if config.db.pool_metrics_enabled
        else None
    )
    db.configure_db(config.db, metrics_client=pool_metrics_client)
    reconcile.register_outbox_reconciler()
    api_guards.register_update_guards()
    log.info(
        "Configured the database",
        pool_size=config.db.pool_size,
        max_overflow=config.db.max_overflow,
        pool_timeout=config.db.pool_timeout,
        pool_metrics_enabled=config.db.pool_metrics_enabled,
        engine_echo=config.db.engine_echo,
    )

    configure_emf_backend(config.metrics)
    log.info(
        "Configured the metrics backend",
        namespace=config.metrics.namespace,
        environment=config.metrics.environment.value,
    )

    configure_patreon(config)
    log.info(
        "Configured the Patreon integration",
        campaign_id=config.patreon.campaign_id,
        # The count is what an operator sets during a key rotation (`new,old`); the keys themselves
        # decrypt every stored token.
        encryption_keys=len(config.patreon.encryption_keys()),
        request_timeout_seconds=config.patreon.request_timeout_seconds,
        supporter_min_cents=config.patreon.supporter_min_cents,
        patron_min_cents=config.patreon.patron_min_cents,
        organizer_min_cents=config.patreon.organizer_min_cents,
    )

    # Adopt the hosts-only group settings so the supporter-check job can remove lapsed hosts;
    # a no-op until a chat id is configured.
    hosts_group.configure(config.bot)
    if hosts_group.is_configured():
        log.info("Configured the hosts-only group", enabled=True, chat_id=hosts_group.chat_id())
    else:
        log.info("Configured the hosts-only group", enabled=False, reason="no_chat_id_configured")

    bot = build_bot(config.bot)
    broadcast_bot = build_broadcast_bot(config.bot)
    log.info(
        "Built the events bots",
        broadcast_max_rate=config.bot.broadcast_max_rate,
        retries_on_throttle=config.bot.retries_on_throttle,
        api_call_log_enabled=config.bot.api_call_log_enabled,
    )

    asyncio.run(run_all_tasks(intervals, bot, broadcast_bot, config.bot.admin_tg_ids, start_time))
