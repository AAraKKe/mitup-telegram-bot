from collections.abc import Iterator

import pytest
from structlog.testing import capture_logs

from mitup_bot.cli.commands.recurrent_events import EventType
from mitup_bot.monitoring import MetricsClient
from tests.helpers.monitoring import MetricAssertions, make_test_metrics_client


@pytest.fixture(autouse=True)
def capture_structlog() -> Iterator[None]:
    """Keep the job's structlog emissions off the real pipeline, mirroring the rest of the CLI
    suite: a bare emission trips the xdist + json-report reporter under coverage."""
    with capture_logs():
        yield


@pytest.fixture
def metrics_client() -> MetricsClient:
    return make_test_metrics_client(base_dimensions={"EventType": EventType.SEND_BROADCASTS.value})


@pytest.fixture
def metrics(metrics_client: MetricsClient) -> MetricAssertions:
    return MetricAssertions(metrics_client)
