import asyncio
from dataclasses import dataclass
from enum import Enum
from random import uniform
from time import perf_counter
from typing import assert_never

import click
from aws_embedded_metrics.environment.environment_detector import resolve_environment
from aws_embedded_metrics.unit import Unit
from pydantic import BaseModel
from telegram import constants
from telegram.ext import AIORateLimiter, Defaults, ExtBot

from mitup_bot import db
from mitup_bot.cli import generate_stats, notify_meetings, user_cleanup
from mitup_bot.config import BotConfig, Config, Env, EnvVariablesConfigProvider, TomlConfigProvider
from mitup_bot.monitoring.metrics import MetricKey, MitupMetricsLogger, configure_metrics

DEFAULT_USER_CLEANUP_INTERVAL = 3600
DEFAULT_GENERATE_STATS_INTERVAL = 3600
DEFAULT_NOTIFY_MEETINGS_START = 60


class EventType(Enum):
    USER_CLEANUP = "UserCleanup"
    GENERATE_STATS = "Stats"
    NOTIFY_START_MEETING = "NotifyStartMeeting"


@dataclass
class IntervalsConfiguration:
    user_cleanup: int
    notify_start_meeting: int
    generate_stats: int

    def get(self, event_type: EventType) -> int:
        match event_type:
            case EventType.USER_CLEANUP:
                return self.user_cleanup
            case EventType.NOTIFY_START_MEETING:
                return self.notify_start_meeting
            case EventType.GENERATE_STATS:
                return self.generate_stats
            case never:
                assert_never(never)


class MaintainanceEvent(BaseModel):
    event_type: EventType
    env: Env = Env.PROD


def build_bot(config: BotConfig) -> ExtBot:
    return ExtBot(
        token=config.token.get_secret_value(),
        rate_limiter=AIORateLimiter(max_retries=config.retries_on_throttle),
        defaults=Defaults(parse_mode=constants.ParseMode.MARKDOWN_V2),
    )


async def launch_event(event: MaintainanceEvent, bot: ExtBot, metrics: MitupMetricsLogger) -> None:
    match event.event_type:
        case EventType.USER_CLEANUP:
            user_cleanup.run(bot, metrics)
        case EventType.NOTIFY_START_MEETING:
            await notify_meetings.run(bot, metrics)
        case EventType.GENERATE_STATS:
            generate_stats.run(bot, metrics)
        case never:
            assert_never(never)


async def handle_maintainance(event: MaintainanceEvent) -> None:
    config = Config.from_providers(
        EnvVariablesConfigProvider(),
        TomlConfigProvider(env=event.env),
    )

    db.configure_db(config.db, skip_if_initialized=True)
    configure_metrics(config.metrics)

    metrics = MitupMetricsLogger(resolve_environment)
    metrics.set_dimensions({"EventType": event.event_type.value})

    bot = build_bot(config.bot)

    start_time = perf_counter()
    fault = False
    try:
        await launch_event(event, bot, metrics)
    except Exception:
        fault = True
        metrics.add_stack_trace("exception")
    finally:
        metrics.put_metric(MetricKey.FAULT.value, 1 if fault else 0, unit=Unit.COUNT.value)
        metrics.put_metric(MetricKey.TIME.value, (perf_counter() - start_time) * 1000, unit=Unit.MILLISECONDS.value)
        await metrics.flush()


async def run_periodic(
    interval: int,
    event: MaintainanceEvent,
    time_before_start: float | None = None,
):
    # If no time provided add 1% interval jitter
    time_before_start = time_before_start if time_before_start is not None else uniform(0, interval * 0.01)
    await asyncio.sleep(time_before_start)

    # Run the coroutine indefinitely
    while True:
        await handle_maintainance(event)
        await asyncio.sleep(interval)


async def run_all_tasks(intervals: IntervalsConfiguration, env: Env, start_time: float):
    async with asyncio.TaskGroup() as tg:
        for event_type in EventType:
            tg.create_task(
                run_periodic(
                    intervals.get(event_type),
                    time_before_start=start_time,
                    event=MaintainanceEvent(event_type=event_type, env=env),
                )
            )


@click.command()
@click.option(
    "--user-cleanup-interval",
    default=DEFAULT_USER_CLEANUP_INTERVAL,
    help="Interval in seconds for user cleanup",
    show_default=True,
)
@click.option(
    "--notify-meeting-interval",
    default=DEFAULT_NOTIFY_MEETINGS_START,
    help="Interval in seconds to send notifications about meetings starting soon",
    show_default=True,
)
@click.option(
    "--generate-stats-interval",
    default=DEFAULT_GENERATE_STATS_INTERVAL,
    help="Interval in seconds to generate bots stats",
    show_default=True,
)
@click.option(
    "--env",
    default=Env.DEV,
    type=click.Choice(Env, case_sensitive=False),
    help="Environment to execute the command with",
    show_default=True,
)
@click.option(
    "--start-time",
    default=0,
    type=float,
    help="Time before starting the first event",
)
def cli(
    user_cleanup_interval: int, notify_meeting_interval: int, env: Env, start_time: float, generate_stats_interval: int
):
    """This method is used when launching notifications locally as a container"""
    intervals = IntervalsConfiguration(
        user_cleanup=user_cleanup_interval,
        notify_start_meeting=notify_meeting_interval,
        generate_stats=generate_stats_interval,
    )
    asyncio.run(run_all_tasks(intervals, env, start_time))
