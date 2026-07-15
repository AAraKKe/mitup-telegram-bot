import asyncio
from dataclasses import dataclass
from enum import Enum
from random import uniform
from time import perf_counter
from typing import assert_never
from uuid import uuid4

import structlog
from telegram.ext import AIORateLimiter, ExtBot

from mitup_bot import api_guards, db, hosts_group, patreon, reconcile
from mitup_bot.api_wrapper import BotAdapter, TelegramApiWrapper, build_api
from mitup_bot.config import BotConfig, Config, Env, EnvVariablesConfigProvider, TomlConfigProvider
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
from mitup_bot.monitoring import EmfBackend, MetricKey, MetricsClient, MetricUnit, configure_emf_backend

log = structlog.get_logger(__name__)

DEFAULT_USER_CLEANUP_INTERVAL = 3600
DEFAULT_GENERATE_STATS_INTERVAL = 3600
DEFAULT_NOTIFY_MEETINGS_START = 60
DEFAULT_DEACTIVATE_MEETINGS_INTERVAL = 60
DEFAULT_MEETUPS_CLEANUP_INTERVAL = 86400  # 24 hours
DEFAULT_NOTIFY_MEETING_STARTED_INTERVAL = 60
DEFAULT_SEND_BROADCASTS_INTERVAL = 60
DEFAULT_SUPPORTER_CHECK_INTERVAL = 86400  # 24 hours


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
    """Wire the token cipher and Patreon runtime for the events process when a section is present.

    Lets the SUPPORTER_CHECK job decrypt/encrypt tokens and read the live config; a no-op otherwise,
    keeping the process bootable without Patreon."""
    if config.patreon is None:
        log.info("Patreon section absent, skipping Patreon setup")
        return
    configure_token_encryption(*config.patreon.encryption_keys())
    patreon.configure(config.patreon)


def build_bot(config: BotConfig) -> ExtBot:
    return ExtBot(
        token=config.token.get_secret_value(),
        rate_limiter=AIORateLimiter(max_retries=config.retries_on_throttle),
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
    with structlog.contextvars.bound_contextvars(flow=event_type.value, event_type=event_type.value, run_id=run_id):
        try:
            db.set_connection_context(event_type.value)
            await dispatch_event(event_type, api, client, admin_tg_ids)
        except Exception:
            fault = True
            # Emit the fault before attaching the trace: add_stack_trace only reaches EMF loggers
            # that already exist, and this emit is what creates the dimensionless global logger.
            # The trace itself must be captured inside the except block — the EMF library reads
            # sys.exc_info(), which Python clears once the block exits.
            client.emit(MetricKey.FAULT, 1, MetricUnit.COUNT, emit_global=True)
            client.add_stack_trace("exception")
            log.exception("Recurrent event run failed")
        finally:
            # emit_global adds a dimensionless copy of Fault/Time so a single Mitup/Events alarm can
            # watch "any event type is failing"/aggregate run duration; EventType stays as an EMF
            # property on those copies for per-event breakdown in Logs Insights.
            if not fault:
                client.emit(MetricKey.FAULT, 0, MetricUnit.COUNT, emit_global=True)
            client.emit(MetricKey.TIME, (perf_counter() - start_time) * 1000, MetricUnit.MILLISECONDS, emit_global=True)
            client.emit(MetricKey.DB_CONNECTIONS_LEAKED, db.get_open_connections(event_type.value), MetricUnit.COUNT)
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


async def run_all_tasks(
    intervals: IntervalsConfiguration,
    bot: ExtBot,
    broadcast_bot: ExtBot,
    admin_tg_ids: list[int],
    start_time: float,
):
    async with asyncio.TaskGroup() as tg:
        for event_type in EventType:
            tg.create_task(
                run_periodic(
                    intervals.get(event_type),
                    time_before_start=start_time,
                    event_type=event_type,
                    bot=select_bot(event_type, bot, broadcast_bot),
                    admin_tg_ids=admin_tg_ids,
                )
            )


def run_events(env: Env, intervals: IntervalsConfiguration, start_time: float):
    """Bootstrap the events process and run every recurrent job on its interval until cancelled."""
    config = Config.from_providers(
        EnvVariablesConfigProvider(),
        TomlConfigProvider(env=env),
    )

    pool_metrics_client = MetricsClient(EmfBackend()) if config.db.pool_metrics_enabled else None
    db.configure_db(config.db, metrics_client=pool_metrics_client)
    reconcile.register_outbox_reconciler()
    api_guards.register_update_guards()
    configure_logging(env, Component.EVENTS, config.app.log_level)
    configure_emf_backend(config.metrics)
    configure_patreon(config)
    # Adopt the hosts-only group settings so the supporter-check job can remove lapsed hosts;
    # a no-op until a chat id is configured.
    hosts_group.configure(config.bot)

    bot = build_bot(config.bot)
    broadcast_bot = build_broadcast_bot(config.bot)

    asyncio.run(run_all_tasks(intervals, bot, broadcast_bot, config.bot.admin_tg_ids, start_time))
