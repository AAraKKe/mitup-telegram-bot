import logging

from rich.console import Console

from mitup_bot.config import Env
from mitup_bot.monitoring import MetricKey
from mitup_bot.utils.mitup_types import TMitupContext

console = Console()


def handler(context: TMitupContext, error: Exception, env: Env):
    # This is the error handler that will receive every exception that is raised

    # Emit an error metric for the current update both including the error type and a general
    # error metric to aggregate all error types
    error_class = error.__class__.__name__
    context.put_metric(MetricKey.FAULT.with_prefix(error_class), 1)
    context.put_metric(MetricKey.FAULT, 1)
    context.put_custom_metric(MetricKey.FAULT, 1)

    context.metrics_engine.add_stack_trace()

    # If we are in development mode, lets print the exception when it happens
    if env is Env.DEV:
        # Print exception with rich logger
        logging.exception("An error occurred while handling the update.")
