import logging
from collections.abc import Generator

import pytest
import structlog

from mitup_bot.config import Env
from mitup_bot.logging_config import configure_logging


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
    configure_logging(Env.PROD, "DEBUG")

    assert logging.getLogger().level == logging.DEBUG  # 10


def test_force_overrides_preexisting_root_handler():
    # Simulate the AWS Lambda runtime which pre-installs a root handler — without force=True
    # basicConfig would be a no-op and neither the level nor the handler would change.
    root = logging.getLogger()
    dummy_handler = logging.StreamHandler()
    root.handlers[:] = [dummy_handler]
    root.setLevel(logging.CRITICAL)  # 50

    configure_logging(Env.PROD, "DEBUG")

    assert root.level == logging.DEBUG  # 10, not 50
    assert dummy_handler not in root.handlers


def test_installs_single_stream_handler_with_processor_formatter():
    """Regardless of env, configure_logging installs exactly one StreamHandler on the root whose
    formatter is a structlog ProcessorFormatter (replaces the old RichHandler/basicConfig setup)."""
    configure_logging(Env.DEV, "INFO")

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
        (Env.SAMPLE, structlog.processors.JSONRenderer),
    ],
    ids=["dev", "prod", "sample"],
)
def test_final_renderer_depends_on_env(
    env: Env, expected_renderer: type[structlog.dev.ConsoleRenderer] | type[structlog.processors.JSONRenderer]
):
    """Dev gets the human-friendly ConsoleRenderer; every other env ships structured JSON."""
    configure_logging(env, "INFO")

    renderer = final_renderer(logging.getLogger().handlers[0])
    assert isinstance(renderer, expected_renderer)


def test_unknown_level_falls_back_to_info():
    configure_logging(Env.PROD, "bogus")

    assert logging.getLogger().level == logging.INFO  # 20


def test_httpx_logger_set_to_warning():
    configure_logging(Env.DEV, "DEBUG")

    assert logging.getLogger("httpx").level == logging.WARNING  # 30


@pytest.mark.parametrize(
    "env,expected_level",
    [
        (Env.DEV, logging.DEBUG),  # 10
        (Env.PROD, logging.WARNING),  # 30
    ],
)
def test_extbot_logger_level_depends_on_env(env: Env, expected_level: int):
    configure_logging(env, "INFO")

    assert logging.getLogger("telegram.ext.ExtBot").level == expected_level
