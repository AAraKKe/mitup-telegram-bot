import asyncio
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager, contextmanager
from typing import Any

from aws_embedded_metrics.config import get_config
from aws_embedded_metrics.logger.metrics_logger import MetricsLogger
from aws_embedded_metrics.logger.metrics_logger_factory import create_metrics_logger
from telegram import Update

from mitup_bot.config import MetricsConfig

metrics_factory = create_metrics_logger


def configure_metrics(config: MetricsConfig, factory: Callable[[], MetricsLogger] | None = None):
    """Set the EMF configuration with the provided configuration.

    If `factory` is especified, it is called to create a metric logger. Useful for testing.
    If it is not defined, the `create_metrics_logger` from `aws_embedded_metrics` is used.
    """
    metrics_config = get_config()

    metrics_config.namespace = config.namespace
    metrics_config.environment = config.environment.value

    if factory:  # pragma: no cover, this is only used for testing
        global metrics_factory
        metrics_factory = factory

    logging.info(f"Metrics Configuration set: {config}")


def __prepare_logger(
    dimensions: dict[str, str] | None = None, properties: dict[str, Any] | None = None
) -> MetricsLogger:
    logger = metrics_factory()
    # Always remove the default dimensions, we don't want to include dimensions we don't control
    logger.set_dimensions(use_default=False)

    if dimensions:
        logger.put_dimensions(dimensions)
    if properties:
        for key, value in properties.items():
            logger.set_property(key, value)
    return logger


@contextmanager
def metrics_context(dimensions: dict[str, str] | None = None, properties: dict[str, Any] | None = None):
    """
    This is intended only in cases where we want to emit a metric outside a callback handler. MitupContext includes a
    logger that is setup for every update that is to be processed.

    For metrics emission from within a callback handler, use the `context.metrics` property.
    """
    logger = __prepare_logger(dimensions=dimensions, properties=properties)
    yield logger
    asyncio.run(logger.flush())


@asynccontextmanager
async def async_metrics_context(dimensions: dict[str, str] | None = None, properties: dict[str, Any] | None = None):
    """
    This is intended only in cases where we want to emit a metric outside a callback handler. MitupContext includes a
    logger that is setup for every update that is to be processed.

    For metrics emission from within a callback handler, use the `context.metrics` property.
    """
    logger = __prepare_logger(dimensions=dimensions, properties=properties)
    yield logger
    await logger.flush()


def properties_from_update(update: Update) -> dict[str, Any]:
    """Create a dictionary with the properties from the provided Update."""
    chat_id = update.effective_chat.id if update.effective_chat else None
    user_id = update.effective_user.id if update.effective_user else None
    callback_data = update.callback_query.data if update.callback_query else None

    return {
        "ChatId": chat_id,
        "UserId": user_id,
        "CallbackData": callback_data,
        "Update": update.to_dict(),
    }


def create_metrics_from_update(update: Update) -> MetricsLogger:
    """Create a MetricsLogger with the provided Update."""
    return __prepare_logger(properties=properties_from_update(update))
