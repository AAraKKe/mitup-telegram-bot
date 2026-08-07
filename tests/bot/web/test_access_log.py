"""Accounting for requests that reached no declared route.

A scanner sweep must cost one metric sample and one summary line per interval and nothing per
request, so the tests below pin the states that property depends on: a request records its target
without writing anything, what one interval remembers stays bounded whatever the caller sends, a
publication aggregates the total, names the busiest targets and resets, a quiet interval stays
silent, and shutdown does not swallow a partial interval."""

import logging
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from structlog.testing import capture_logs

from mitup_bot.config import RunModes
from mitup_bot.monitoring import MetricKey, MetricsClient
from mitup_bot.web.access_log import UnroutedInterval, UnroutedRequests, emit_pending, unrouted_request_reporting
from tests.helpers import MetricAssertions, build_ptb_app_mock, build_test_web_app, build_web_client, log_record
from tests.helpers.monitoring import FlushCountingBackend

SCANNER_PATH = "/wp-content/plugins/wp-file-manager/readme.txt"

# The summary's event name and the logger it comes from, spelled out here rather than imported from
# the module under test, so a rename fails these assertions instead of travelling silently into them.
SWEEP_SUMMARY_EVENT = "Unrouted request sweep summary"
ACCESS_LOG_LOGGER = "mitup_bot.web.access_log"


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


async def test_unrouted_request_only_records_its_target(ptb_app: MagicMock):
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
    # of its own), and not a flush. All the request leaves behind is the in-process accounting.
    unrouted = web_app.state.unrouted_requests
    assert unrouted.pending == 1
    assert list(unrouted.targets) == [("GET", SCANNER_PATH)]
    assert not logs, f"an unrouted request must write no line, captured {[entry['event'] for entry in logs]}"
    MetricAssertions(metrics_client).assert_not_emitted(name=MetricKey.UNROUTED_REQUEST)
    assert backend.flush_count == 0


async def test_query_string_never_reaches_the_accounting(web_app: FastAPI):
    # A near-miss on a real route — a trailing-slash Patreon OAuth callback — carries a live
    # authorization code in its query, and the summary line would carry it into the stream. The
    # recorded target below is the anchor: it exists, and it stops at the path.
    async with build_web_client(web_app) as client:
        response = await client.get(f"{SCANNER_PATH}?code=live-oauth-code")

    assert response.status_code == 404
    assert list(web_app.state.unrouted_requests.targets) == [("GET", SCANNER_PATH)]


async def test_target_parts_are_truncated():
    # Method and path are both caller-controlled text: a scanner can send megabytes of either, and
    # untruncated they would sit in memory until the interval ends and then land in a log line.
    unrouted = UnroutedRequests()
    unrouted.record("PROPFINDPROPFINDPROPFIND", "/" + "a" * 120)

    interval = unrouted.drain()

    # UNROUTED_METHOD_MAX_CHARS = 16 → "PROPFIND" twice; UNROUTED_PATH_MAX_CHARS = 100 → the
    # leading slash plus 99 of the 120 "a".
    assert interval.top_targets == [(("PROPFINDPROPFIND", "/" + "a" * 99), 1)]


async def test_tracking_stops_at_the_cap_and_the_overflow_is_tallied():
    # A wordlist has no upper bound on distinct paths, so past the cap a new target may only add to
    # a tally — while a target already being counted keeps counting, or a sweep's busiest paths
    # would stop growing the moment the cap was reached.
    unrouted = UnroutedRequests()
    for index in range(50):  # UNROUTED_TRACKED_TARGETS
        unrouted.record("GET", f"/probe/{index}")
    unrouted.record("GET", "/probe/0")
    unrouted.record("GET", "/probe/fresh")
    unrouted.record("GET", "/probe/other")

    interval = unrouted.drain()

    assert interval.total == 53  # 50 distinct + 1 repeat + 2 beyond the cap
    assert interval.tracked_targets == 50
    assert interval.untracked_count == 2
    # The repeat is the only target counted twice, so it leads the ranking.
    assert interval.top_targets[0] == (("GET", "/probe/0"), 1 + 1)


async def test_draining_detaches_the_interval_and_resets_every_part():
    # The reporter drains on a timer forever, so anything a drain leaves behind is carried into
    # every later interval and inflates each of them.
    unrouted = UnroutedRequests()
    unrouted.record("GET", SCANNER_PATH)
    unrouted.record("GET", "/probe/fresh")
    unrouted.record("GET", "/probe/fresh")

    assert unrouted.drain() == UnroutedInterval(
        total=3,
        tracked_targets=2,
        untracked_count=0,
        top_targets=[(("GET", "/probe/fresh"), 2), (("GET", SCANNER_PATH), 1)],
    )
    assert unrouted.drain() == UnroutedInterval(total=0, tracked_targets=0, untracked_count=0, top_targets=[])


async def test_publication_aggregates_the_interval_and_resets(metrics: MetricAssertions, metrics_client: MetricsClient):
    unrouted = UnroutedRequests()
    for index in range(2000):
        unrouted.record("GET", SCANNER_PATH if index % 2 else "/probe/other")

    assert emit_pending(unrouted, metrics_client) is True

    # One sample carrying the whole interval rather than one per request: that ratio is the saving.
    metrics.assert_emitted(name=MetricKey.UNROUTED_REQUEST, value=2000, dimensions={}, dimensions_exact=True)
    assert len(metrics_client.records) == 1
    # The record exists, and the probed target reaches no part of it: on the metric plane a path is
    # unbounded cardinality, which is one billed series per value a scanner invents.
    assert "wp-file-manager" not in str(metrics_client.records)
    assert unrouted.pending == 0
    assert not unrouted.targets


async def test_summary_line_names_the_busiest_targets(caplog: pytest.LogCaptureFixture, metrics_client: MetricsClient):
    # The metric sizes a sweep but says nothing about what it went after; the line is where that
    # lands, capped at the top targets so the busiest scan still costs one bounded line a minute.
    caplog.set_level(logging.WARNING)
    unrouted = UnroutedRequests()
    for rank in range(12):
        for _ in range(rank + 1):
            unrouted.record("GET", f"/probe/{rank}")

    assert emit_pending(unrouted, metrics_client) is True

    summaries = [record for record in caplog.records if record.message == SWEEP_SUMMARY_EVENT]
    assert len(summaries) == 1, f"expected one summary line, captured {[record.message for record in caplog.records]}"
    summary = summaries[0]
    assert summary.levelname == "WARNING"
    assert summary.__dict__["unrouted_count"] == 78  # 1 + 2 + ... + 12
    assert summary.__dict__["tracked_targets"] == 12
    assert summary.__dict__["untracked_count"] == 0
    # UNROUTED_REPORTED_TARGETS = 10 of the 12, busiest first: ranks 11 down to 2, whose counts run
    # 12 down to 3.
    assert summary.__dict__["top_targets"] == [
        {"method": "GET", "path": f"/probe/{rank}", "count": rank + 1} for rank in range(11, 1, -1)
    ]


async def test_summary_line_reports_what_the_cap_left_out(
    caplog: pytest.LogCaptureFixture, metrics_client: MetricsClient
):
    # Without the overflow tally a capped interval reads as a sweep of exactly 50 targets, and the
    # difference between a scan of 50 paths and one of thousands is the whole signal.
    caplog.set_level(logging.WARNING)
    unrouted = UnroutedRequests()
    for index in range(70):
        unrouted.record("GET", f"/probe/{index}")

    assert emit_pending(unrouted, metrics_client) is True

    summary = log_record(caplog, SWEEP_SUMMARY_EVENT)
    assert summary.__dict__["unrouted_count"] == 70
    assert summary.__dict__["tracked_targets"] == 50  # UNROUTED_TRACKED_TARGETS
    assert summary.__dict__["untracked_count"] == 20
    assert len(summary.__dict__["top_targets"]) == 10  # UNROUTED_REPORTED_TARGETS


async def test_quiet_interval_emits_nothing(
    caplog: pytest.LogCaptureFixture, metrics: MetricAssertions, metrics_client: MetricsClient
):
    # Absence is the signal for "nobody probed us". A zero every interval would put back the
    # per-interval EMF document — and the summary line beside it — that aggregating exists to
    # remove. The published interval below anchors the assertion: the same call, one interval
    # later with nothing recorded, must leave both planes exactly as it found them.
    caplog.set_level(logging.WARNING)
    unrouted = UnroutedRequests()
    unrouted.record("GET", SCANNER_PATH)

    assert emit_pending(unrouted, metrics_client) is True
    published = [record for record in caplog.records if record.name == ACCESS_LOG_LOGGER]
    assert [record.message for record in published] == [SWEEP_SUMMARY_EVENT]

    assert emit_pending(unrouted, metrics_client) is False

    assert [record for record in caplog.records if record.name == ACCESS_LOG_LOGGER] == published
    metrics.assert_emitted(name=MetricKey.UNROUTED_REQUEST, times=1)


async def test_shutdown_publishes_the_partial_interval(metrics: MetricAssertions, metrics_client: MetricsClient):
    # The accounting standing at shutdown belongs to an interval the reporter never reaches, so
    # leaving the block has to drain it — otherwise the tail of a sweep vanishes on every deploy.
    unrouted = UnroutedRequests()
    async with unrouted_request_reporting(unrouted, metrics_client):
        unrouted.record("GET", SCANNER_PATH)

    metrics.assert_emitted(name=MetricKey.UNROUTED_REQUEST, value=1)
    assert unrouted.pending == 0
