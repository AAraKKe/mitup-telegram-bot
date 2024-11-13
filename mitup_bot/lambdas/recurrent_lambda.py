import asyncio
from enum import Enum
from time import perf_counter
from typing import Any, assert_never

from aws_embedded_metrics.environment.environment_detector import resolve_environment
from aws_embedded_metrics.unit import Unit
from pydantic import BaseModel
from telegram import constants
from telegram.ext import AIORateLimiter, Defaults, ExtBot

from mitup_bot import db
from mitup_bot.config import BotConfig, Config, Env, EnvVariablesConfigProvider, TomlConfigProvider
from mitup_bot.monitoring.metrics import MetricKey, MitupMetricsLogger, configure_metrics

from . import notify_meetings, user_cleanup


class EventType(Enum):
    USER_CLEANUP = "UserCleanup"
    NOTIFY_START_MEETING = "NotifyStartMeeting"


class MaintainanceEvent(BaseModel):
    event_type: EventType
    env: Env


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
        case _:
            assert_never()


async def handle_maintainance(event: dict[str, Any], context: Any) -> None:
    event_object = MaintainanceEvent.model_validate(event)

    config = Config.from_providers(
        EnvVariablesConfigProvider(),
        TomlConfigProvider(env=event_object.env),
    )

    db.configure_db(config.db, skip_if_initialized=True)
    configure_metrics(config.metrics)

    metrics = MitupMetricsLogger(resolve_environment)
    metrics.set_dimensions({"EventType": event_object.event_type.value})

    bot = build_bot(config.bot)

    start_time = perf_counter()
    fault = False
    try:
        await launch_event(event_object, bot, metrics)
    except Exception:
        fault = True
        metrics.add_stack_trace("exception")
    finally:
        metrics.put_metric(MetricKey.FAULT.value, 1 if fault else 0)
        metrics.put_metric(MetricKey.TIME.value, (perf_counter() - start_time) * 1000, unit=Unit.MILLISECONDS.value)
        await metrics.flush()


def lambda_handler(event: dict[str, Any], context: Any) -> None:
    asyncio.run(handle_maintainance(event, context))
