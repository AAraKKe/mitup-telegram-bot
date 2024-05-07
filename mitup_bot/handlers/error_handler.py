from aws_embedded_metrics.logger.metrics_logger import MetricsLogger
from telegram.ext import ExtBot

from mitup_bot.custom_context import MitupContext
from mitup_bot.monitoring import MetricKey


def handler(context: MitupContext[ExtBot, MetricsLogger], error: Exception):
    # This is the error handler that will receive every exception that is raised

    # Emit an error metric for the current update both including the error type and a general
    # error metric to aggregate all error types
    context.metrics.add_stack_trace("exception")
    error_class = error.__class__.__name__
    context.put_metric(MetricKey.FAULT.with_prefix(error_class), 1)
    context.put_metric(MetricKey.FAULT, 1)
