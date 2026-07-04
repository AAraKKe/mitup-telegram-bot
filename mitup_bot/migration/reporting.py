"""Per-row + per-phase progress reporter for the Rails migration tool.

Two output styles are supported:
- `console`: rich-formatted, with a rule per phase and coloured ✔/✘ marks per row.
- `log`: plain `logging.info` lines with the same ✔/✘ marks (no rich markup), suitable
  for CloudWatch / Lambda where rich's TTY heuristics don't apply.

A `MetricsFlusher` can be attached so CloudWatch metrics are flushed mid-phase instead
of buffered until the end — every emitted EMF line gets its own timestamp, which is
what makes progress visible on a dashboard.
"""

import logging
from enum import StrEnum
from typing import TYPE_CHECKING

from rich.console import Console

if TYPE_CHECKING:
    from mitup_bot.migration.phases import PhaseSummary
    from mitup_bot.monitoring import MetricsClient


class MetricsFlusher:
    """Flushes the `MetricsClient` every `flush_every` ticks.

    Without this, EMF logs the entire phase as a single line with one timestamp, hiding
    the migration's progression. Calling `flush()` mid-phase forces a new EMF line with
    a fresh timestamp so each batch shows up as its own point on the timeline.
    """

    def __init__(self, metrics: MetricsClient, flush_every: int = 50):
        self._metrics = metrics
        self._flush_every = max(flush_every, 1)
        self._pending = 0

    async def tick(self):
        self._pending += 1
        if self._pending >= self._flush_every:
            await self._flush_now()

    async def final(self):
        if self._pending > 0:
            await self._flush_now()

    async def _flush_now(self):
        await self._metrics.flush()
        self._pending = 0


class OutputMode(StrEnum):
    CONSOLE = "console"
    LOG = "log"


log = logging.getLogger("mitup_bot.migration")


class MigrationReporter:
    """Emits per-phase rules and per-row outcome lines in the configured style."""

    def __init__(self, mode: OutputMode, console: Console | None = None, flusher: MetricsFlusher | None = None):
        self._mode = mode
        self._console = console
        self._flusher = flusher

    async def phase_start(self, phase: str, table: str):
        label = f"{phase} → {table}" if phase != table else phase
        if self._mode is OutputMode.CONSOLE:
            self._require_console().rule(f"[bold cyan]Phase: {label}[/]")
        else:
            log.info("=== Phase: %s ===", label)
        # Phase boundary: flush whatever has accumulated so the new phase's metrics start
        # on a fresh timeline point.
        await self._final_flush()

    async def row_inserted(self, table: str, old_id: int, new_id: int):
        self._emit_row("✔", "green", table, old_id, f"inserted → new_id={new_id}")
        await self._tick()

    async def row_skipped(self, table: str, old_id: int, reason: str):
        self._emit_row("✘", "yellow", table, old_id, f"skipped: {reason}")
        await self._tick()

    async def row_failed(self, table: str, old_id: int, error: str):
        self._emit_row("✘", "red", table, old_id, f"failed: {error}")
        await self._tick()

    async def phase_end(self, summary: PhaseSummary):
        text = (
            f"{summary.table}: read={summary.read} inserted={summary.inserted} "
            f"skipped={summary.skipped} failed={summary.failed} duration_ms={summary.duration_ms}"
        )
        if self._mode is OutputMode.CONSOLE:
            self._require_console().print(f"[bold]{text}[/]")
        else:
            log.info(text)
        await self._final_flush()

    async def archive_table(self, table: str, rows: int, bytes_written: int):
        text = f"archive:{table} rows={rows} bytes={bytes_written}"
        if self._mode is OutputMode.CONSOLE:
            self._require_console().print(f"[green]✔[/] {text}")
        else:
            log.info("✔ %s", text)
        await self._tick()

    async def verification(self, deltas: dict[str, int]):
        if self._mode is OutputMode.CONSOLE:
            console = self._require_console()
            console.rule("[bold cyan]Phase: verify[/]")
            for table, delta in deltas.items():
                symbol, colour = ("✔", "green") if delta == 0 else ("✘", "red")
                console.print(f"[{colour}]{symbol}[/] {table}: delta={delta}")
        else:
            log.info("=== Phase: verify ===")
            for table, delta in deltas.items():
                mark = "✔" if delta == 0 else "✘"
                log.info("%s %s: delta=%d", mark, table, delta)
        await self._final_flush()

    async def _tick(self):
        if self._flusher is not None:
            await self._flusher.tick()

    async def _final_flush(self):
        if self._flusher is not None:
            await self._flusher.final()

    def _emit_row(self, symbol: str, colour: str, table: str, old_id: int, message: str):
        if self._mode is OutputMode.CONSOLE:
            self._require_console().print(f"[{colour}]{symbol}[/] {table}#{old_id} {message}")
        else:
            log.info("%s %s#%d %s", symbol, table, old_id, message)

    def _require_console(self) -> Console:
        if self._console is None:
            raise RuntimeError("Console reporter requires a rich.Console instance")
        return self._console
