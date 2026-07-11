import logging
from enum import StrEnum

import structlog

from mitup_bot.config import Env


class Component(StrEnum):
    """The process that produced a log line. Bound once for the lifetime of the process."""

    BOT = "bot"
    EVENTS = "events"
    LAMBDA = "lambda"
    CLI = "cli"


def add_component(component: Component) -> structlog.typing.Processor:
    """Stamp the process-level component onto every record. This is a processor, not a contextvar,
    so it survives the asyncio task and thread boundaries that reset contextvars."""

    def processor(
        logger: structlog.typing.WrappedLogger,
        method_name: str,
        event_dict: structlog.typing.EventDict,
    ) -> structlog.typing.EventDict:
        event_dict.setdefault("component", component.value)
        return event_dict

    return processor


def shared_processors(component: Component) -> list[structlog.typing.Processor]:
    """Processors shared by structlog-native loggers and foreign (stdlib) records routed through
    ProcessorFormatter. merge_contextvars pulls in the request/invocation fields bound once per
    entry point so every downstream log line carries them without threading a logger around."""
    return [
        structlog.contextvars.merge_contextvars,
        add_component(component),
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]


def final_renderer(env: Env) -> structlog.typing.Processor:
    """Env selects the final renderer: dev gets human-friendly colorized console output (this
    replaces the old RichHandler), prod ships structured JSON for the cloud aggregator."""
    if env is Env.DEV:
        return structlog.dev.ConsoleRenderer(colors=True)
    return structlog.processors.JSONRenderer()


def build_root_handler(env: Env, component: Component) -> logging.Handler:
    """Build the root stdlib handler whose ProcessorFormatter renders both structlog-native and
    foreign (httpx, telegram, sqlalchemy, ...) records through the same env-selected renderer."""
    formatter = structlog.stdlib.ProcessorFormatter(
        # foreign_pre_chain runs on records from stdlib/3rd-party loggers so they pick up the same
        # context fields and render identically to structlog-native lines.
        foreign_pre_chain=shared_processors(component),
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            final_renderer(env),
        ],
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    return handler


def configure_library_levels(env: Env):
    """Tune noisy third-party loggers."""
    # httpx logs every request at INFO, which floods our logs with HTTP noise.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # ExtBot is quiet by default; raise it to DEBUG in dev to surface bot API traffic.
    logging.getLogger("telegram.ext.ExtBot").setLevel(logging.DEBUG if env is Env.DEV else logging.WARNING)


def configure_logging(env: Env, component: Component, level: str = "INFO"):
    """Lenient on `level` so callers may pass a raw, unvalidated string (e.g. a Lambda
    `LOG_LEVEL` env var); an unrecognized name falls back to INFO instead of raising.
    """
    numeric_level = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)

    structlog.configure(
        processors=[
            *shared_processors(component),
            # Hand off to ProcessorFormatter so structlog-native and stdlib records share one renderer.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # force=True so our handler/level take effect even when a root handler already exists — the AWS
    # Lambda runtime pre-installs one, which otherwise makes basicConfig a no-op.
    logging.basicConfig(level=numeric_level, handlers=[build_root_handler(env, component)], force=True)

    configure_library_levels(env)
