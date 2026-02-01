from collections.abc import Generator
from contextlib import contextmanager
from typing import Protocol

from aws_embedded_metrics.unit import Unit
from telegram.ext import ExtBot

from mitup_bot.monitoring import MetricKey


class ContextOrBotAdapter(Protocol):
    """
    Protocol defining the interface for the object necessary to interact with the Telegram API.

    This is used to support both MitupContext and ExtBot for flexibility.
    """

    @contextmanager
    def with_time_metric(self, prefix: str, handler_metrics: bool = False) -> Generator[None]: ...

    def emit_metric(
        self,
        name: str | MetricKey,
        value: float = 1.0,
        unit: Unit = Unit.COUNT,
        *,
        dimensions: dict[str, str] | None = None,
        include_handler_dimensions: bool = True,
        properties: dict[str, str | int | float | None] | None = None,
        include_update_properties: bool = True,
        emit_global: bool = False,
    ): ...

    async def flush_metrics(self): ...

    @property
    def bot(self) -> ExtBot: ...
