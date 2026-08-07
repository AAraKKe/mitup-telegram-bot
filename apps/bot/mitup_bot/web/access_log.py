"""Accounting for requests that reached no declared route.

The endpoint is published by Certificate Transparency and sits behind a layer-4 load balancer that
cannot filter paths, so untargeted vulnerability scanners reach uvicorn directly and sweep it — one
such host sent 21k requests across 11k invented paths in ninety minutes. Anything written per
request turns a sweep into the log stream, and an EMF emission is a log line of its own, so the
accounting is kept in memory and published once an interval instead: one metric sample, and one
summary log line naming the most-probed request targets.

A request that a route served is not accounted for here at all: the endpoint that handled it
narrates itself, and the update trace covers a Telegram delivery end to end.

What one interval may remember and publish is bounded on every axis the caller controls. The method
and path are truncated, at most `UNROUTED_TRACKED_TARGETS` distinct targets are counted (the rest
collapse into a single untracked tally), and the summary line reports only the top
`UNROUTED_REPORTED_TARGETS` of them — so the busiest scan costs one metric document and one log
line a minute, and can grow neither the process memory nor the log stream past those bounds. The
query string never reaches this module: callers pass the bare path, because a near-miss on a real
route (a trailing-slash Patreon callback) would otherwise capture a live OAuth code.
"""

import asyncio
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass

import structlog
from fastapi import Request
from starlette.routing import Route

from mitup_bot.monitoring.client import MetricsClient
from mitup_bot.monitoring.metric_keys import MetricKey

log = structlog.get_logger(__name__)

# How often the accumulated accounting is published. One sample a minute is fine granularity for a
# sweep that runs for tens of minutes, and it caps what the busiest scan can cost: one EMF document
# and one summary log line a minute regardless of how many requests landed in it.
UNROUTED_REPORT_INTERVAL_SECONDS = 60

# The most distinct (method, path) targets one interval counts individually; requests beyond it are
# tallied without their target. A scanner's wordlist is unbounded, its useful signal is not: the cap
# keeps the counter's memory fixed while the totals still size the sweep.
UNROUTED_TRACKED_TARGETS = 50
# How many of the tracked targets the summary line names, busiest first.
UNROUTED_REPORTED_TARGETS = 10
# Both parts of a target are caller-controlled text, so both are truncated. The longest standard
# HTTP methods run 10 characters; anything longer is scanner junk that the prefix identifies.
UNROUTED_METHOD_MAX_CHARS = 16
UNROUTED_PATH_MAX_CHARS = 100


@dataclass(frozen=True)
class UnroutedInterval:
    """One interval's accounting, detached from the live counter by `UnroutedRequests.drain`."""

    total: int
    tracked_targets: int
    untracked_count: int
    top_targets: list[tuple[tuple[str, str], int]]


class UnroutedRequests:
    """The unrouted-request accounting accumulated since the last publication.

    Recording and draining both run to completion between await points on the one event loop the
    app serves from, so the state needs no lock.
    """

    def __init__(self):
        self.pending = 0
        self.targets: Counter[tuple[str, str]] = Counter()
        self.untracked = 0

    def record(self, method: str, path: str):
        self.pending += 1
        target = (method[:UNROUTED_METHOD_MAX_CHARS], path[:UNROUTED_PATH_MAX_CHARS])
        if target in self.targets or len(self.targets) < UNROUTED_TRACKED_TARGETS:
            self.targets[target] += 1
        else:
            self.untracked += 1

    def drain(self) -> UnroutedInterval:
        interval = UnroutedInterval(
            total=self.pending,
            tracked_targets=len(self.targets),
            untracked_count=self.untracked,
            top_targets=self.targets.most_common(UNROUTED_REPORTED_TARGETS),
        )
        self.pending = 0
        self.targets = Counter()
        self.untracked = 0
        return interval


def matched_a_route(request: Request) -> bool:
    """Whether the router matched a declared route for *request*.

    The router writes the route it picked into the scope it was handed, and `BaseHTTPMiddleware`
    hands that same dict downstream, so the answer is available once `call_next` has returned.
    """
    return isinstance(request.scope.get("route"), Route)


def emit_pending(unrouted: UnroutedRequests, metrics_client: MetricsClient) -> bool:
    """Publish the interval accumulated so far, and report whether there was anything to publish.

    A quiet interval buffers nothing and writes nothing. An EMF emission is itself a log line, so a
    zero every interval would reinstate the volume that aggregating removed, and absence reads
    correctly here: no sample means nobody probed us.
    """
    interval = unrouted.drain()
    if not interval.total:
        return False
    metrics_client.emit(MetricKey.UNROUTED_REQUEST, interval.total)
    log.warning(
        "Unrouted request sweep summary",
        unrouted_count=interval.total,
        tracked_targets=interval.tracked_targets,
        untracked_count=interval.untracked_count,
        top_targets=[
            {"method": method, "path": path, "count": count} for (method, path), count in interval.top_targets
        ],
    )
    return True


async def report_unrouted_requests(unrouted: UnroutedRequests, metrics_client: MetricsClient):
    while True:
        await asyncio.sleep(UNROUTED_REPORT_INTERVAL_SECONDS)
        if emit_pending(unrouted, metrics_client):
            await metrics_client.flush()


@asynccontextmanager
async def unrouted_request_reporting(unrouted: UnroutedRequests, metrics_client: MetricsClient) -> AsyncIterator[None]:
    """Publish *unrouted* on an interval for the duration of the block, draining it on the way out.

    The accounting standing when the app stops belongs to a partial interval that the reporter will
    never reach, so the exit buffers it into the client and leaves it to the lifespan's closing
    flush — which runs after this block — rather than flushing again here.
    """
    reporter = asyncio.create_task(report_unrouted_requests(unrouted, metrics_client))
    try:
        yield
    finally:
        reporter.cancel()
        with suppress(asyncio.CancelledError):
            await reporter
        emit_pending(unrouted, metrics_client)
