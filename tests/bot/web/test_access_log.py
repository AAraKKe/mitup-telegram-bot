"""Accounting for requests that reached no declared route.

A scanner sweep must cost one metric sample per interval and nothing per request, so the tests below
pin the states that property depends on: a request counts without writing anything, a publication
aggregates and resets, a quiet interval stays silent, and shutdown does not swallow a partial
count."""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from structlog.testing import capture_logs

from mitup_bot.config import RunModes
from mitup_bot.monitoring import MetricKey, MetricsClient
from mitup_bot.web.access_log import UnroutedRequests, emit_pending, unrouted_request_reporting
from tests.helpers import MetricAssertions, build_ptb_app_mock, build_test_web_app, build_web_client
from tests.helpers.monitoring import FlushCountingBackend

SCANNER_PATH = "/wp-content/plugins/wp-file-manager/readme.txt"


@pytest.fixture
def ptb_app() -> MagicMock:
    return build_ptb_app_mock()


@pytest.fixture
def web_app(ptb_app: MagicMock, metrics_client: MetricsClient) -> FastAPI:
    return build_test_web_app(
        ptb_app=ptb_app,
        secret_token="test-secret",
        metrics_client=metrics_client,
        run_mode=RunModes.WEBHOOK,
    )


async def test_unrouted_request_only_increments_the_counter(ptb_app: MagicMock):
    # A counting backend rather than the shared fixture, because "did not flush" is half of what
    # this proves: a flush walks every logger the backend has cached and writes a document for
    # each, so flushing a request that emitted nothing costs the volume aggregating exists to save.
    backend = FlushCountingBackend()
    metrics_client = MetricsClient(backend, record_history=True)
    web_app = build_test_web_app(ptb_app=ptb_app, metrics_client=metrics_client, run_mode=RunModes.WEBHOOK)

    with capture_logs() as logs:
        async with build_web_client(web_app) as client:
            response = await client.get(SCANNER_PATH)

    assert response.status_code == 404
    # Nothing is written at request time — not a line, not a record (an EMF emission is a log line
    # of its own), and not a flush. All the request leaves behind is the in-process count.
    assert web_app.state.unrouted_requests.pending == 1
    assert not logs, f"an unrouted request must write no line, captured {[entry['event'] for entry in logs]}"
    MetricAssertions(metrics_client).assert_not_emitted(name=MetricKey.UNROUTED_REQUEST)
    assert backend.flush_count == 0

    # The probed path is caller-controlled text of unbounded cardinality, so it must reach neither
    # plane: no captured line and no emitted record may carry any part of it.
    assert "wp-file-manager" not in str(logs)
    assert "wp-file-manager" not in str(metrics_client.records)


async def test_publication_aggregates_the_interval_and_resets(metrics: MetricAssertions, metrics_client: MetricsClient):
    unrouted = UnroutedRequests()
    for _ in range(2000):
        unrouted.record()

    assert emit_pending(unrouted, metrics_client) is True

    # One sample carrying the whole interval rather than one per request: that ratio is the saving.
    metrics.assert_emitted(name=MetricKey.UNROUTED_REQUEST, value=2000, dimensions={}, dimensions_exact=True)
    assert len(metrics_client.records) == 1
    assert unrouted.pending == 0


async def test_quiet_interval_emits_nothing(metrics_client: MetricsClient):
    # Absence is the signal for "nobody probed us". A zero every interval would put back the
    # per-interval EMF document that aggregating exists to remove.
    assert emit_pending(UnroutedRequests(), metrics_client) is False
    assert not metrics_client.records


async def test_shutdown_publishes_the_partial_interval(metrics: MetricAssertions, metrics_client: MetricsClient):
    # The count standing at shutdown belongs to an interval the reporter never reaches, so leaving
    # the block has to drain it — otherwise the tail of a sweep vanishes on every deploy.
    unrouted = UnroutedRequests()
    async with unrouted_request_reporting(unrouted, metrics_client):
        unrouted.record()

    metrics.assert_emitted(name=MetricKey.UNROUTED_REQUEST, value=1)
    assert unrouted.pending == 0
