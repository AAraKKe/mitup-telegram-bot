from typing import Any, Protocol

from telegram.ext import ExtBot

from mitup_bot.monitoring import MetricKey
from mitup_bot.monitoring.units import MetricUnit


class ContextOrBotAdapter(Protocol):
    """
    Protocol defining the interface for the object necessary to interact with the Telegram API.

    This is used to support both MitupContext and ExtBot for flexibility.
    """

    def emit_metric(
        self,
        name: str | MetricKey,
        value: float = 1.0,
        unit: MetricUnit = MetricUnit.COUNT,
        *,
        dimensions: dict[str, str] | None = None,
        properties: dict[str, Any] | None = None,
    ): ...

    async def flush_metrics(self): ...

    @property
    def bot(self) -> ExtBot: ...
