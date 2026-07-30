import io
import json
import logging
from collections.abc import Callable, Generator
from typing import cast

import pytest
import structlog

from mitup_bot.config import Env
from mitup_bot.logging_config import UNKNOWN_RELEASE, Component, configure_logging
from tests.helpers.logs import drop_cached_logger_binds


@pytest.fixture(autouse=True)
def restore_logging_state() -> Generator[None]:
    """`logging.basicConfig` mutates process-global state (root level and handlers) plus the
    `httpx` and `telegram.ext.ExtBot` logger levels. Without this, calls leak into other
    tests and the rest of the suite.
    """
    root = logging.getLogger()
    saved_root_handlers = root.handlers[:]
    saved_root_level = root.level
    saved_httpx_level = logging.getLogger("httpx").level
    saved_extbot_level = logging.getLogger("telegram.ext.ExtBot").level
    try:
        yield
    finally:
        root.handlers[:] = saved_root_handlers
        root.setLevel(saved_root_level)
        logging.getLogger("httpx").setLevel(saved_httpx_level)
        logging.getLogger("telegram.ext.ExtBot").setLevel(saved_extbot_level)


def final_renderer(handler: logging.Handler) -> structlog.typing.Processor:
    """Pull the env-selected final renderer out of an installed handler's ProcessorFormatter.

    build_root_handler wires the formatter's `processors` list as
    [remove_processors_meta, <final renderer>], so the renderer is always the last entry.
    """
    formatter = handler.formatter
    assert isinstance(formatter, structlog.stdlib.ProcessorFormatter)
    return formatter.processors[-1]


def test_applies_requested_level_to_root():
    configure_logging(Env.PROD, Component.BOT, "DEBUG")

    assert logging.getLogger().level == logging.DEBUG  # 10


def test_force_overrides_preexisting_root_handler():
    # Simulate the AWS Lambda runtime which pre-installs a root handler — without force=True
    # basicConfig would be a no-op and neither the level nor the handler would change.
    root = logging.getLogger()
    dummy_handler = logging.StreamHandler()
    root.handlers[:] = [dummy_handler]
    root.setLevel(logging.CRITICAL)  # 50

    configure_logging(Env.PROD, Component.BOT, "DEBUG")

    assert root.level == logging.DEBUG  # 10, not 50
    assert dummy_handler not in root.handlers


def test_installs_single_stream_handler_with_processor_formatter():
    """Regardless of env, configure_logging installs exactly one StreamHandler on the root whose
    formatter is a structlog ProcessorFormatter (replaces the old RichHandler/basicConfig setup)."""
    configure_logging(Env.DEV, Component.BOT, "INFO")

    handlers = logging.getLogger().handlers
    assert len(handlers) == 1
    handler = handlers[0]
    assert type(handler) is logging.StreamHandler
    assert isinstance(handler.formatter, structlog.stdlib.ProcessorFormatter)


@pytest.mark.parametrize(
    "env, expected_renderer",
    [
        (Env.DEV, structlog.dev.ConsoleRenderer),
        (Env.PROD, structlog.processors.JSONRenderer),
    ],
    ids=["dev", "prod"],
)
def test_final_renderer_depends_on_env(
    env: Env, expected_renderer: type[structlog.dev.ConsoleRenderer] | type[structlog.processors.JSONRenderer]
):
    """Dev gets the human-friendly ConsoleRenderer; every other env ships structured JSON."""
    configure_logging(env, Component.BOT, "INFO")

    renderer = final_renderer(logging.getLogger().handlers[0])
    assert isinstance(renderer, expected_renderer)


def test_unknown_level_falls_back_to_info():
    configure_logging(Env.PROD, Component.BOT, "bogus")

    assert logging.getLogger().level == logging.INFO  # 20


def test_httpx_logger_set_to_warning():
    configure_logging(Env.DEV, Component.BOT, "DEBUG")

    assert logging.getLogger("httpx").level == logging.WARNING  # 30


@pytest.mark.parametrize(
    "env,expected_level",
    [
        (Env.DEV, logging.DEBUG),  # 10
        (Env.PROD, logging.WARNING),  # 30
    ],
)
def test_extbot_logger_level_depends_on_env(env: Env, expected_level: int):
    configure_logging(env, Component.BOT, "INFO")

    assert logging.getLogger("telegram.ext.ExtBot").level == expected_level


def render_one_line(component: Component, emit: Callable[[], None], release: str | None = None) -> dict[str, object]:
    """Configure prod (JSON) logging with `component`, run `emit`, and return the single rendered
    line parsed back from JSON. Redirects the installed handler at a buffer so the assertion reads
    exactly what the processor chain produced — and so the lines `configure_logging` writes about
    itself, which precede the redirect, stay out of the buffer."""
    configure_logging(Env.PROD, component, release=release)
    handler = cast("logging.StreamHandler[io.StringIO]", logging.getLogger().handlers[0])
    buffer = io.StringIO()
    handler.setStream(buffer)
    emit()
    handler.flush()
    return json.loads(buffer.getvalue())


def probe_line(release: str | None = None) -> dict[str, object]:
    return render_one_line(Component.BOT, lambda: structlog.get_logger("native.probe").info("probe"), release)


def self_description(capsys: pytest.CaptureFixture[str], level: str = "INFO") -> dict[str, dict[str, object]]:
    """Configure prod logging and return the lines the pipeline wrote about itself, by event name.

    `configure_logging` emits through the handler it just installed, which bound the `sys.stderr`
    pytest had already replaced — so its own lines are read back off the captured stream rather
    than through a redirect that can only be installed after the call returns.
    """
    # The module logger those lines go through caches its bound chain on first use, and a sibling
    # test in this module has usually already frozen it onto an earlier configuration. A process
    # calls `configure_logging` once, so only a test needs this.
    drop_cached_logger_binds()
    configure_logging(Env.PROD, Component.BOT, level, release="ci-9f3a1c2")
    logging.getLogger().handlers[0].flush()
    lines = [json.loads(line) for line in capsys.readouterr().err.splitlines() if line]
    return {str(line["event"]): line for line in lines}


def test_component_stamped_on_structlog_native_record():
    record = render_one_line(Component.BOT, lambda: structlog.get_logger("native.probe").info("probe"))

    assert record["component"] == "bot"


def test_component_stamped_on_foreign_stdlib_record():
    """The foreign_pre_chain path renders records from stdlib/third-party loggers and is the one that
    silently regresses, so assert `component` lands on it independently of the native path."""
    record = render_one_line(Component.EVENTS, lambda: logging.getLogger("foreign.probe").warning("probe"))

    assert record["component"] == "events"


# --- Release marker ---


def test_release_stamped_on_every_line():
    """The release rides as a processor stamp rather than a field per call site: a rolling deploy
    has two builds writing into one log group, and every line has to say which wrote it."""
    assert probe_line(release="ci-9f3a1c2")["release"] == "ci-9f3a1c2"


def test_release_falls_back_to_the_unknown_marker():
    """A process started outside `mb deploy` carries no build identity. The marker keeps that
    answerable, where an absent field would be indistinguishable from a dropped one."""
    assert probe_line()["release"] == UNKNOWN_RELEASE


# --- The pipeline's self-description ---


def test_configured_logging_describes_the_installed_pipeline(capsys: pytest.CaptureFixture[str]):
    """The head of every stream: what this deploy is, how it renders, and at what level."""
    line = self_description(capsys, level="DEBUG")["Configured logging"]

    assert line["level"] == "info"
    assert line["component"] == Component.BOT.value
    assert line["release"] == "ci-9f3a1c2"
    assert line["env"] == Env.PROD.value
    assert line["effective_level"] == "DEBUG"
    assert line["renderer"] == "json"
    assert "reason" not in line


def test_an_unknown_level_says_so_instead_of_silently_running_at_info(capsys: pytest.CaptureFixture[str]):
    """The fallback is deliberate and silent, which is how a debug session ends up reading a stream
    that never had the lines in it."""
    line = self_description(capsys, level="verbose")["Configured logging"]

    assert line["level"] == "warning"
    assert line["reason"] == "unknown_level_name"
    assert line["requested_level"] == "VERBOSE"
    assert line["effective_level"] == "INFO"


def test_third_party_levels_are_recorded_with_the_pipeline(capsys: pytest.CaptureFixture[str]):
    """Their silence is our configuration decision: without the line, "no httpx lines" reads as
    "no outbound calls"."""
    line = self_description(capsys)["Tuned third-party log levels"]

    assert line["httpx"] == "WARNING"
    assert line["ext_bot"] == "WARNING"  # prod; dev raises ExtBot to DEBUG


# --- Bot-token redaction ---

BOT_TOKEN = "8100200300:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
BOT_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"


def test_bot_token_redacted_from_a_url_in_the_event_string():
    """The token is glued straight onto the `bot` path segment, so the surrounding URL must survive
    while the credential itself does not."""
    record = render_one_line(Component.BOT, lambda: structlog.get_logger("native.probe").info(BOT_API_URL))

    assert record["event"] == "https://api.telegram.org/bot[redacted-bot-token]/sendMessage"


def test_bot_token_redacted_from_a_bare_field_value():
    record = render_one_line(
        Component.BOT, lambda: structlog.get_logger("native.probe").info("configured", token=BOT_TOKEN)
    )

    assert record["token"] == "[redacted-bot-token]"


def test_bot_token_redacted_on_foreign_stdlib_record():
    """httpx and PTB log through stdlib, so the foreign_pre_chain needs the same scrubbing as the
    structlog-native chain."""
    record = render_one_line(Component.BOT, lambda: logging.getLogger("foreign.probe").warning(BOT_API_URL))

    assert BOT_TOKEN not in json.dumps(record)


def test_bot_token_redacted_inside_a_rendered_traceback():
    """Redaction runs after format_exc_info, so a token carried in an exception message is scrubbed
    out of the rendered traceback rather than only out of the event string."""

    def emit():
        try:
            raise RuntimeError(f"request to {BOT_API_URL} failed")
        except RuntimeError:
            structlog.get_logger("native.probe").exception("Telegram call failed")

    record = render_one_line(Component.BOT, emit)

    assert BOT_TOKEN not in json.dumps(record)
    assert "[redacted-bot-token]" in str(record["exception"])


def test_value_without_a_token_shape_passes_through_untouched():
    """A colon between digits is ordinary in timestamps and connection strings, so the pattern has
    to be narrow enough to leave them alone."""
    value = "postgresql://mitup_app@db.internal:5432/mitup at 2026-07-28T10:22:33.123456Z"

    record = render_one_line(Component.BOT, lambda: structlog.get_logger("native.probe").info("probe", field=value))

    assert record["field"] == value


def test_non_string_values_pass_through_untouched():
    """Scalars are left exactly as bound — the processor neither coerces nor drops them."""
    record = render_one_line(
        Component.BOT,
        lambda: structlog.get_logger("native.probe").info("probe", meeting_id=7, ratio=1.5, missing=None),
    )

    assert record["meeting_id"] == 7
    assert record["ratio"] == 1.5
    assert record["missing"] is None


def test_bot_token_redacted_inside_nested_containers():
    """Fields are routinely bound as dicts and lists, and the renderer serializes them in full, so
    scanning only top-level strings would leave the credential readable one level down."""
    record = render_one_line(
        Component.BOT,
        lambda: structlog.get_logger("native.probe").info(
            "probe", payload={"request": {"url": BOT_API_URL}}, tokens=[BOT_TOKEN]
        ),
    )

    assert BOT_TOKEN not in json.dumps(record)
    assert record["payload"] == {"request": {"url": "https://api.telegram.org/bot[redacted-bot-token]/sendMessage"}}
    assert record["tokens"] == ["[redacted-bot-token]"]
