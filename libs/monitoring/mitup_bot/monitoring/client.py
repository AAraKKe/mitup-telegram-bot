from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from mitup_bot.monitoring.backend import MetricsBackend
from mitup_bot.monitoring.metric_keys import Feature, MetricKey
from mitup_bot.monitoring.record import MetricRecord
from mitup_bot.monitoring.units import MetricUnit


class MetricsClient:
    def __init__(
        self,
        backend: MetricsBackend,
        base_dimensions: dict[str, str] | None = None,
        *,
        record_history: bool = False,
    ):
        """Build a metrics client.

        `record_history=True` keeps every emitted `MetricRecord` in memory so tests can
        introspect them via `client.records`. **Do not enable in production** — long-lived
        processes (workers, bots, one-shot migrations of large datasets) will leak memory
        proportional to the number of metrics emitted.
        """
        self._backend = backend
        self._record_history = record_history
        self._records: list[MetricRecord] = []
        self._base_dimensions = base_dimensions or {}

    def emit(
        self,
        name: str | MetricKey = MetricKey.COUNT,
        value: float = 1.0,
        unit: MetricUnit = MetricUnit.COUNT,
        *,
        dimensions: dict[str, str] | None = None,
        properties: dict[str, Any] | None = None,
        emit_global: bool = False,
    ):
        """Emit a metric record through the backend.

        `emit_global=True` additionally emits a dimensionless copy that drops the client's
        `base_dimensions` but keeps them as EMF properties. This gives an aggregate series
        (no dimensions) to alarm on across every base-dimension value, while Logs Insights can
        still break the aggregate down by the promoted properties.
        """
        merged_dims = {**self._base_dimensions, **(dimensions or {})}
        self._emit_record(name, value, unit, merged_dims, properties or {})

        if emit_global:
            global_properties = {**self._base_dimensions, **(properties or {})}
            self._emit_record(name, value, unit, dict(dimensions or {}), global_properties)

    def _emit_record(
        self,
        name: str | MetricKey,
        value: float,
        unit: MetricUnit,
        dimensions: dict[str, str],
        properties: dict[str, Any],
    ):
        record = MetricRecord(
            name=str(name),
            value=value,
            unit=unit,
            dimensions=frozenset(dimensions.items()),
            properties=properties,
        )
        if self._record_history:
            self._records.append(record)
        self._backend.emit(record)

    def emit_feature(
        self,
        feature: Feature,
        value: float = 1.0,
        name: str | MetricKey = MetricKey.COUNT,
        unit: MetricUnit = MetricUnit.COUNT,
        properties: dict[str, Any] | None = None,
    ):
        self.emit(
            name,
            value,
            unit,
            dimensions={"Feature": str(feature)},
            properties=properties,
        )

    def set_global_property(self, key: str, value: Any):
        self._backend.set_global_property(key, value)

    async def flush(self):
        await self._backend.flush()

    @property
    def records(self) -> list[MetricRecord]:
        return self._records


ambient_client: ContextVar[MetricsClient | None] = ContextVar("ambient_metrics_client", default=None)


@contextmanager
def bound_metrics_client(client: MetricsClient) -> Generator[None]:
    """Publish `client` as the ambient one for the duration of the block.

    Code far below the entry point — the instrumented Telegram request class, the Patreon client —
    emits into whichever invocation is on the stack without a client being threaded through PTB or
    httpx. Context variables propagate down the await chain and into tasks the block spawns, and
    the previous value is restored on exit so nothing leaks into the next invocation handled by a
    reused worker task.
    """
    token = ambient_client.set(client)
    try:
        yield
    finally:
        ambient_client.reset(token)


def current_metrics_client() -> MetricsClient | None:
    """The ambient client, or None outside any instrumented invocation (a process's own startup
    calls run there, and they have no flush window to join)."""
    return ambient_client.get()
