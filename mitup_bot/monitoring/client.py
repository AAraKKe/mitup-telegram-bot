from typing import Any

from telegram import Update

from mitup_bot.monitoring.backend import MetricsBackend
from mitup_bot.monitoring.metric_keys import Feature, MetricKey
from mitup_bot.monitoring.record import MetricRecord
from mitup_bot.monitoring.units import MetricUnit


def properties_from_update(update: Update) -> dict[str, Any]:
    """Create a dictionary with the properties from the provided Update."""
    return {
        "Update": update_fields_from_update(update),
    }


def update_fields_from_update(update: Update) -> dict[str, Any]:
    values = {}

    # Callback data fields
    if cb := update.callback_query:
        values["callback"] = {"data": cb.data}

    if user := update.effective_user:
        values["user"] = {
            "tg_user_id": user.id,
            "username": user.username,
        }

    if message := update.effective_message:
        values["message"] = {
            "text": message.text,
            "markup": message.reply_markup.to_dict() if message.reply_markup is not None else None,
        }

    # Show inline query queries
    if query := update.inline_query:
        values["inline_query"] = {"query": query.query}

    return values


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
    ):
        merged_dims = {**self._base_dimensions, **(dimensions or {})}
        record = MetricRecord(
            name=str(name),
            value=value,
            unit=unit,
            dimensions=frozenset(merged_dims.items()),
            properties=properties or {},
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

    def add_stack_trace(self, key: str = "exception"):
        self._backend.add_stack_trace(key)

    async def flush(self):
        await self._backend.flush()

    @property
    def records(self) -> list[MetricRecord]:
        return self._records
