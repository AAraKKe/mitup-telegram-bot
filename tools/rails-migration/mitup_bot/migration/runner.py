import logging
from typing import Any

from mitup_bot import db
from mitup_bot.monitoring import MetricsClient

from .archive import ArchiveWriter
from .modes import MigrationMode
from .phases import run_migration
from .rails_reader import RailsReader
from .reporting import MigrationReporter, OutputMode

ALL_PHASES = ("users", "meetups", "joins", "invitations", "messages", "archive", "verify")

log = logging.getLogger("mitup_bot.migration")


class DryRunDiscard(Exception):
    """Raised inside a db.begin() block to trigger rollback while preserving the report."""


def configure_migration_logging(output: OutputMode):
    """Apply a sane log setup for the migration tool.

    SQLAlchemy's `engine_echo` config installs its own INFO handler on `sqlalchemy.engine`
    that dumps every statement + parameters. We walk every existing `sqlalchemy.*` logger,
    drop its handlers, and pin it to WARNING so the migration tool's own progress lines are
    the only output. Same treatment for boto/urllib chatter.
    """
    fmt = "%(message)s" if output is OutputMode.CONSOLE else "%(asctime)s %(levelname)s %(name)s :: %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt, force=True)

    silenced_prefixes = ("sqlalchemy", "boto3", "botocore", "urllib3", "s3transfer")
    for name in list(logging.Logger.manager.loggerDict):
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in silenced_prefixes):
            log = logging.getLogger(name)
            log.setLevel(logging.WARNING)
            for handler in list(log.handlers):
                log.removeHandler(handler)
            log.propagate = True


async def run_migration_pipeline(
    rails_url: str,
    archive_s3_uri: str | None,
    batch_size: int,
    phases: tuple[str, ...],
    mode: MigrationMode,
    metrics: MetricsClient,
    reporter: MigrationReporter,
    connect_timeout: int = 10,
) -> dict[str, Any]:
    """Execute the pipeline inside a single DB transaction.

    Live mode lets the `db.begin()` context manager commit when all phases finish; a
    crash mid-run rolls back the entire transaction. In dry-run mode a sentinel exception
    fires the same rollback path so nothing is persisted, while the report is still
    returned to the caller. Per-row failures are isolated via SAVEPOINTs inside each
    phase, so they don't abort the outer transaction.
    """
    log.info("Starting migration pipeline (mode=%s, phases=%s)", mode, ", ".join(phases))
    writer = ArchiveWriter(s3_uri=archive_s3_uri, dry_run=mode is MigrationMode.DRY_RUN)
    report: dict[str, Any] = {}
    try:
        # RailsReader stays a sync context manager (psycopg's blocking API), so it cannot
        # join the async with.
        with RailsReader(rails_url, batch_size=batch_size, connect_timeout=connect_timeout) as reader:
            log.info("Connecting to target DB…")
            async with db.begin() as session:
                report = await run_migration(
                    session=session,
                    reader=reader,
                    archive_writer=writer,
                    metrics=metrics,
                    mode=mode,
                    phases=phases,
                    reporter=reporter,
                )
                if mode is MigrationMode.DRY_RUN:
                    raise DryRunDiscard()
    except DryRunDiscard:
        pass
    log.info("Migration pipeline complete (mode=%s)", mode)
    return report


async def run_pipeline_then_flush(
    rails_url: str,
    archive_s3_uri: str | None,
    batch_size: int,
    phases: tuple[str, ...],
    mode: MigrationMode,
    metrics: MetricsClient,
    reporter: MigrationReporter,
    connect_timeout: int = 10,
) -> dict[str, Any]:
    """Single event-loop entry point: the engine and the final metrics flush share one loop."""
    report = await run_migration_pipeline(
        rails_url, archive_s3_uri, batch_size, phases, mode, metrics, reporter, connect_timeout
    )
    await metrics.flush()
    return report


def has_migration_failures(report: dict[str, Any]) -> bool:
    return any(phase.get("failed", 0) for phase in report.get("phases", {}).values())
