from typing import cast

import freezegun
from aws_embedded_metrics.environment.environment_detector import resolve_environment
from rich.console import Console

from mitup_bot.config import MetricsConfig, MetricsEnv
from mitup_bot.monitoring.metrics import (
    MitupMetricsEngine,
    MitupMetricsLogger,
    RichConsoleSink,
    RichEnvironment,
    configure_metrics,
)


async def test_rich_environment():
    # Need to configure monitoring with a custom logger to test the output and override the global test configuration
    # from contest.py
    configure_metrics(
        MetricsConfig(namespace="MyNamespace", environment=MetricsEnv.RICH),
    )

    engine = MitupMetricsEngine(logger_provider=lambda _: MitupMetricsLogger(resolve_environment))

    environment = cast(RichEnvironment, await resolve_environment())
    sink = cast(RichConsoleSink, environment.sink)
    # Remove colors from terminal to test output
    sink.console = Console(force_interactive=False, force_terminal=False)

    with sink.console.capture() as capture, freezegun.freeze_time("2024-01-01 12:00:00"):
        engine.put_metric("MyMetric", 1.0)
        await engine.flush_metrics()

    expected_formatted_text = (
        "{\n"
        '  "_aws": {\n'
        '    "Timestamp": 1704110400000,\n'
        '    "CloudWatchMetrics": [\n'
        "      {\n"
        '        "Dimensions": [],\n'
        '        "Metrics": [\n'
        "          {\n"
        '            "Name": "MyMetric",\n'
        '            "Unit": "Count"\n'
        "          }\n"
        "        ],\n"
        '        "Namespace": "MyNamespace"\n'
        "      }\n"
        "    ]\n"
        "  },\n"
        '  "MyMetric": 1.0\n'
        "}\n"
    )

    assert capture.get() == expected_formatted_text
