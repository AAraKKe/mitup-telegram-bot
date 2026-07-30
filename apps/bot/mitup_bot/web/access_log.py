"""Accounting for requests that reached no declared route.

The endpoint is published by Certificate Transparency and sits behind a layer-4 load balancer that
cannot filter paths, so untargeted vulnerability scanners reach uvicorn directly and sweep it — one
such host sent 21k requests across 11k invented paths in ninety minutes. Anything written per
request turns a sweep into the log stream, and an EMF emission is a log line of its own, so the
count is kept in memory and published once an interval instead.

A request that a route served is not accounted for here at all: the endpoint that handled it
narrates itself, and the update trace covers a Telegram delivery end to end.

Nothing here reads the request path. It is caller-controlled text with no upper bound on distinct
values, so it reaches neither the stream nor a metric; whether the router matched anything is the
only thing this module learns about a request.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import Request
from starlette.routing import Route

from mitup_bot.monitoring.client import MetricsClient
from mitup_bot.monitoring.metric_keys import MetricKey

# How often the accumulated count is published. One sample a minute is fine granularity for a sweep
# that runs for tens of minutes, and it caps what the busiest scan can cost: one EMF document a
# minute regardless of how many requests landed in it.
UNROUTED_REPORT_INTERVAL_SECONDS = 60


class UnroutedRequests:
    """The count of unrouted requests accumulated since the last publication.

    Incrementing and draining both run to completion between await points on the one event loop the
    app serves from, so the count needs no lock.
    """

    def __init__(self):
        self.pending = 0

    def record(self):
        self.pending += 1

    def drain(self) -> int:
        count, self.pending = self.pending, 0
        return count


def matched_a_route(request: Request) -> bool:
    """Whether the router matched a declared route for *request*.

    The router writes the route it picked into the scope it was handed, and `BaseHTTPMiddleware`
    hands that same dict downstream, so the answer is available once `call_next` has returned.
    """
    return isinstance(request.scope.get("route"), Route)


def emit_pending(unrouted: UnroutedRequests, metrics_client: MetricsClient) -> bool:
    """Buffer the count accumulated so far, and report whether there was any to buffer.

    A quiet interval buffers nothing and writes nothing. An EMF emission is itself a log line, so a
    zero every interval would reinstate the volume that aggregating removed, and absence reads
    correctly here: no sample means nobody probed us.
    """
    count = unrouted.drain()
    if not count:
        return False
    metrics_client.emit(MetricKey.UNROUTED_REQUEST, count)
    return True


async def report_unrouted_requests(unrouted: UnroutedRequests, metrics_client: MetricsClient):
    while True:
        await asyncio.sleep(UNROUTED_REPORT_INTERVAL_SECONDS)
        if emit_pending(unrouted, metrics_client):
            await metrics_client.flush()


@asynccontextmanager
async def unrouted_request_reporting(unrouted: UnroutedRequests, metrics_client: MetricsClient) -> AsyncIterator[None]:
    """Publish *unrouted* on an interval for the duration of the block, draining it on the way out.

    The count standing when the app stops belongs to a partial interval that the reporter will never
    reach, so the exit buffers it into the client and leaves it to the lifespan's closing flush —
    which runs after this block — rather than flushing again here.
    """
    reporter = asyncio.create_task(report_unrouted_requests(unrouted, metrics_client))
    try:
        yield
    finally:
        reporter.cancel()
        with suppress(asyncio.CancelledError):
            await reporter
        emit_pending(unrouted, metrics_client)
