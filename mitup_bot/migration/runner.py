import asyncio
import logging
import os
from typing import Any

from mitup_bot import db
from mitup_bot.config import Config, Env, EnvVariablesConfigProvider, TomlConfigProvider
from mitup_bot.monitoring import EmfBackend, MetricsClient, configure_emf_backend

from .archive import ArchiveWriter
from .modes import MigrationMode
from .phases import run_migration
from .rails_reader import RailsReader
from .reporting import MetricsFlusher, MigrationReporter, OutputMode

ALL_PHASES = ("users", "meetups", "joins", "invitations", "messages", "archive", "verify")


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
) -> dict[str, Any]:
    """Execute the pipeline inside a single DB transaction.

    Live mode lets the `db.begin()` context manager commit when all phases finish; a
    crash mid-run rolls back the entire transaction. In dry-run mode a sentinel exception
    fires the same rollback path so nothing is persisted, while the report is still
    returned to the caller. Per-row failures are isolated via SAVEPOINTs inside each
    phase, so they don't abort the outer transaction.
    """
    writer = ArchiveWriter(s3_uri=archive_s3_uri, dry_run=mode is MigrationMode.DRY_RUN)
    report: dict[str, Any] = {}
    try:
        # RailsReader stays a sync context manager (psycopg's blocking API), so it cannot
        # join the async with.
        with RailsReader(rails_url, batch_size=batch_size) as reader:
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
    return report


async def run_pipeline_then_flush(
    rails_url: str,
    archive_s3_uri: str | None,
    batch_size: int,
    phases: tuple[str, ...],
    mode: MigrationMode,
    metrics: MetricsClient,
    reporter: MigrationReporter,
) -> dict[str, Any]:
    """Single event-loop entry point: the engine and the final metrics flush share one loop."""
    report = await run_migration_pipeline(rails_url, archive_s3_uri, batch_size, phases, mode, metrics, reporter)
    await metrics.flush()
    return report


def has_migration_failures(report: dict[str, Any]) -> bool:
    return any(phase.get("failed", 0) for phase in report.get("phases", {}).values())


def invoke_from_lambda(
    rails_url: str, archive_s3_uri: str | None, *, dry_run: bool, phases: str = ",".join(ALL_PHASES)
):
    """Programmatic entry point so the Lambda wrapper can invoke without going through Click."""
    env_default = os.environ.get("MITUPBOT_ENV", Env.PROD.value)
    selected_phases = tuple(p.strip() for p in phases.split(",") if p.strip())
    mode = MigrationMode.DRY_RUN if dry_run else MigrationMode.LIVE

    config = Config.from_providers(EnvVariablesConfigProvider(), TomlConfigProvider(env=Env(env_default)))
    config.db.engine_echo = False
    configure_migration_logging(OutputMode.LOG)
    db.configure_db(config.db, skip_if_initialized=True)
    configure_emf_backend(config.metrics)

    backend = EmfBackend(base_dimensions={"Tool": "MigrateFromRails"})
    metrics = MetricsClient(backend=backend)
    flush_every = int(os.environ.get("MIGRATE_METRICS_FLUSH_EVERY", "50"))
    flusher = MetricsFlusher(metrics, flush_every=flush_every)
    reporter = MigrationReporter(OutputMode.LOG, flusher=flusher)
    batch_size = int(os.environ.get("MIGRATE_BATCH_SIZE", "1000"))

    report = asyncio.run(
        run_pipeline_then_flush(rails_url, archive_s3_uri, batch_size, selected_phases, mode, metrics, reporter)
    )

    if has_migration_failures(report):
        raise RuntimeError("Migration finished with failed rows. See the audit table for details.")
