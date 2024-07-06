import logging

import telegram
import telegram.ext
from rich.console import Console

from mitup_bot.config import Env
from mitup_bot.custom_context import MitupContext
from mitup_bot.monitoring import MetricKey, MitupMetricsLogger

console = Console()


def handler(context: MitupContext[telegram.ext.ExtBot, MitupMetricsLogger], error: Exception, env: Env):
    # This is the error handler that will receive every exception that is raised

    # Emit an error metric for the current update both including the error type and a general
    # error metric to aggregate all error types
    context.metrics.add_stack_trace("exception")
    error_class = error.__class__.__name__
    context.put_metric(MetricKey.FAULT.with_prefix(error_class), 1)
    context.put_metric(MetricKey.FAULT, 1)

    # If we are in development mode, lets print the exception when it happens
    if env is Env.DEV:
        # Print exception with rich logger
        logging.exception("An error occurred while handling the update.")
