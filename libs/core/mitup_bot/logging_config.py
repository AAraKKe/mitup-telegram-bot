import logging
import re
from enum import StrEnum

import structlog

from mitup_bot.config import Env

log = structlog.get_logger(__name__)

# Stamped as the release when the process was started without one. A rolling deploy keeps two
# builds serving at once, so "which build wrote this line" has to be answerable from the line; a
# literal marker keeps that question answerable ("started outside `mb deploy`") where an absent
# field would be indistinguishable from a field that was dropped.
UNKNOWN_RELEASE = "unknown"

# A Telegram bot token is a numeric bot id, a colon, and a 35-character secret. Matched without a
# leading boundary because the value most likely to reach a log line is a Bot API URL, where the
# token is glued straight onto the `bot` path segment.
BOT_TOKEN_PATTERN = re.compile(r"\d{5,}:[A-Za-z0-9_-]{30,}")

REDACTED_BOT_TOKEN = "[redacted-bot-token]"

# Bounds the walk into container values, so a self-referential or pathologically nested payload
# cannot make a log call recurse without end.
MAX_REDACTION_DEPTH = 6


class Component(StrEnum):
    """The process that produced a log line. Bound once for the lifetime of the process."""

    BOT = "bot"
    EVENTS = "events"
    LAMBDA = "lambda"
    CLI = "cli"


def add_process_identity(component: Component, release: str) -> structlog.typing.Processor:
    """Stamp the process-level component and release onto every record. This is a processor, not a
    contextvar, so it survives the asyncio task and thread boundaries that reset contextvars."""

    def processor(
        logger: structlog.typing.WrappedLogger,
        method_name: str,
        event_dict: structlog.typing.EventDict,
    ) -> structlog.typing.EventDict:
        event_dict.setdefault("component", component.value)
        event_dict.setdefault("release", release)
        return event_dict

    return processor


def redact_value(value: object, depth: int = 0) -> object:
    """Return *value* with every bot-token substring replaced, recursing into container values.

    Fields are routinely bound as dicts and lists — a serialized update, a request payload — and the
    renderer serializes those in full, so scanning only top-level strings would leave the credential
    readable one level down.
    """
    if isinstance(value, str):
        return BOT_TOKEN_PATTERN.sub(REDACTED_BOT_TOKEN, value)
    if depth >= MAX_REDACTION_DEPTH:
        return value
    if isinstance(value, dict):
        return {key: redact_value(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item, depth + 1) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item, depth + 1) for item in value)
    return value


def redact_bot_tokens(
    logger: structlog.typing.WrappedLogger,
    method_name: str,
    event_dict: structlog.typing.EventDict,
) -> structlog.typing.EventDict:
    """Scrub anything shaped like a Telegram bot token out of every value in the record.

    PTB addresses the Bot API as `https://api.telegram.org/bot<token>/<method>`, so a request URL, an
    httpx exception message or a raw error body all carry the credential — and a log group outlives
    the token that leaked into it. This is a floor under every call site rather than a licence to log
    the value. `redact_value` tests for a string first, so the common field costs one isinstance and
    one regex, and scalars come back as the same object; container values are rebuilt either way,
    which is still cheaper than comparing a rebuilt container to decide whether to store it.
    """
    for key, value in event_dict.items():
        event_dict[key] = redact_value(value)
    return event_dict


def shared_processors(component: Component, release: str) -> list[structlog.typing.Processor]:
    """Processors shared by structlog-native loggers and foreign (stdlib) records routed through
    ProcessorFormatter. merge_contextvars pulls in the request/invocation fields bound once per
    entry point so every downstream log line carries them without threading a logger around.
    Redaction runs last, after format_exc_info has rendered the traceback into a string, so a token
    inside a stack trace is covered too."""
    return [
        structlog.contextvars.merge_contextvars,
        add_process_identity(component, release),
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_bot_tokens,
    ]


def final_renderer(env: Env) -> structlog.typing.Processor:
    """Env selects the final renderer: dev gets human-friendly colorized console output (this
    replaces the old RichHandler), prod ships structured JSON for the cloud aggregator."""
    if env is Env.DEV:
        return structlog.dev.ConsoleRenderer(colors=True)
    return structlog.processors.JSONRenderer()


def renderer_name(env: Env) -> str:
    """Name the renderer `final_renderer` installs, so the startup line says how this stream is
    formatted rather than leaving a reader to infer it from the line they are reading."""
    return "console" if env is Env.DEV else "json"


def build_root_handler(env: Env, component: Component, release: str) -> logging.Handler:
    """Build the root stdlib handler whose ProcessorFormatter renders both structlog-native and
    foreign (httpx, telegram, sqlalchemy, ...) records through the same env-selected renderer."""
    formatter = structlog.stdlib.ProcessorFormatter(
        # foreign_pre_chain runs on records from stdlib/3rd-party loggers so they pick up the same
        # context fields and render identically to structlog-native lines.
        foreign_pre_chain=shared_processors(component, release),
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            final_renderer(env),
        ],
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    return handler


def configure_library_levels(env: Env):
    """Tune noisy third-party loggers, and record the levels they were tuned to.

    Their silence is a configuration decision of ours, so it belongs in the stream: without the
    line, "no httpx lines" reads as "no outbound calls" rather than "httpx is pinned at WARNING".
    """
    # httpx logs every request at INFO, which floods our logs with HTTP noise.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # ExtBot is quiet by default; raise it to DEBUG in dev to surface bot API traffic.
    ext_bot_level = logging.DEBUG if env is Env.DEV else logging.WARNING
    logging.getLogger("telegram.ext.ExtBot").setLevel(ext_bot_level)

    log.info(
        "Tuned third-party log levels",
        httpx=logging.getLevelName(logging.WARNING),
        ext_bot=logging.getLevelName(ext_bot_level),
    )


def configure_logging(env: Env, component: Component, level: str = "INFO", release: str | None = None):
    """Install the process-wide logging pipeline and describe it in its own first line.

    Lenient on `level` so callers may pass a raw, unvalidated string (e.g. a Lambda `LOG_LEVEL` env
    var); an unrecognized name falls back to INFO instead of raising. `release` names the build and
    is stamped on every line; a caller that has no build identity gets `UNKNOWN_RELEASE`.
    """
    requested_level = level.upper()
    level_names = logging.getLevelNamesMapping()
    numeric_level = level_names.get(requested_level, logging.INFO)
    resolved_release = release or UNKNOWN_RELEASE

    structlog.configure(
        processors=[
            *shared_processors(component, resolved_release),
            # Hand off to ProcessorFormatter so structlog-native and stdlib records share one renderer.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # force=True so our handler/level take effect even when a root handler already exists — the AWS
    # Lambda runtime pre-installs one, which otherwise makes basicConfig a no-op.
    logging.basicConfig(
        level=numeric_level,
        handlers=[build_root_handler(env, component, resolved_release)],
        force=True,
    )

    # The head of every stream: the pipeline states what it is, which build is writing through it
    # and at what level. `component` and `release` ride on the line from the processor above.
    # `effective_level` rather than `level`: the pipeline's own `add_log_level` owns that key and
    # would overwrite it with this line's severity.
    pipeline = {
        "env": env.value,
        "effective_level": logging.getLevelName(numeric_level),
        "requested_level": requested_level,
        "renderer": renderer_name(env),
    }
    if requested_level in level_names:
        log.info("Configured logging", **pipeline)
    else:
        # A typo'd level quietly resolving to INFO is how a debug session ends up staring at a
        # stream that never had the lines it is looking for.
        log.warning("Configured logging", reason="unknown_level_name", **pipeline)

    configure_library_levels(env)
