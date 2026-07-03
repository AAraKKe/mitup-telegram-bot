import asyncio
import json
import logging
import os
from typing import Any

import click

from mitup_bot import db
from mitup_bot.cli.helpers import console, error, success
from mitup_bot.config import Config, Env, EnvVariablesConfigProvider, TomlConfigProvider
from mitup_bot.migration import (
    ArchiveWriter,
    MetricsFlusher,
    MigrationMode,
    MigrationReporter,
    OutputMode,
    RailsReader,
    run_migration,
)
from mitup_bot.monitoring import EmfBackend, MetricsClient, configure_emf_backend

ALL_PHASES = ("users", "meetups", "joins", "invitations", "messages", "archive", "verify")


def configure_logging(output: OutputMode):
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


class _DryRunDiscard(Exception):
    """Raised inside a db.begin() block to trigger rollback while preserving the report."""


@click.command()
@click.option(
    "--env",
    default=Env.PROD,
    type=click.Choice(choices=Env, case_sensitive=False),
    help="Environment for target-DB config. Defaults to prod since this tool is meant to run inside Lambda.",
)
@click.option(
    "--rails-url",
    envvar="RAILS_DB_URL",
    required=True,
    help="DSN for the legacy Rails Postgres. Reads RAILS_DB_URL from env if not set.",
)
@click.option(
    "--archive-s3-uri",
    envvar="MIGRATE_ARCHIVE_S3_URI",
    help="s3:// URI under which gzipped JSONL dumps of the archived tables will be written.",
)
@click.option(
    "--batch-size",
    default=1000,
    type=int,
    help="Server-side cursor fetch size for the Rails reader.",
)
@click.option(
    "--phases",
    default=",".join(ALL_PHASES),
    help=f"Comma-separated phases to run. Choose any subset of: {', '.join(ALL_PHASES)}.",
)
@click.option(
    "--dry-run/--no-dry-run",
    default=True,
    help="Run the full pipeline but roll back DB writes and skip S3 uploads. Metrics are still emitted. "
    "Defaults to dry-run; pass --no-dry-run for a live cutover.",
)
@click.option(
    "--output",
    "output",
    default=OutputMode.CONSOLE,
    type=click.Choice(choices=OutputMode, case_sensitive=False),
    help="How per-row progress is rendered. `console` uses rich (rule per phase + coloured ✔/✘ per row); "
    "`log` emits the same content as plain log lines. Defaults to `console`.",
)
@click.option(
    "--metrics-flush-every",
    default=50,
    type=int,
    help="Flush CloudWatch metrics every N rows so progress shows up on dashboards instead of being "
    "buffered into one big EMF line per phase. Set to 1 for per-row flush (verbose); higher for less "
    "log volume but coarser timeline resolution.",
)
def cli(
    env: Env,
    rails_url: str,
    archive_s3_uri: str | None,
    batch_size: int,
    phases: str,
    dry_run: bool,
    output: OutputMode,
    metrics_flush_every: int,
):
    """Migrate data from the legacy Rails bot DB into the new schema."""
    selected_phases = tuple(p.strip() for p in phases.split(",") if p.strip())
    unknown = [p for p in selected_phases if p not in ALL_PHASES]
    if unknown:
        error(f"Unknown phase(s): {', '.join(unknown)}. Valid phases: {', '.join(ALL_PHASES)}")
        raise click.Abort()

    mode = MigrationMode.DRY_RUN if dry_run else MigrationMode.LIVE

    config = Config.from_providers(EnvVariablesConfigProvider(), TomlConfigProvider(env=env))
    # Force engine_echo off: dev.toml turns it on, which floods the run with SQL statements.
    config.db.engine_echo = False
    configure_logging(output)
    db.configure_db(config.db, skip_if_initialized=True)
    configure_emf_backend(config.metrics)

    backend = EmfBackend(base_dimensions={"Tool": "MigrateFromRails"})
    metrics = MetricsClient(backend=backend)
    flusher = MetricsFlusher(metrics, flush_every=metrics_flush_every)
    reporter = MigrationReporter(
        output,
        console=console() if output is OutputMode.CONSOLE else None,
        flusher=flusher,
    )

    if mode is MigrationMode.LIVE and "archive" in selected_phases and not archive_s3_uri:
        error("--archive-s3-uri / MIGRATE_ARCHIVE_S3_URI is required when running the archive phase live.")
        raise click.Abort()

    report = asyncio.run(
        run_pipeline_then_flush(rails_url, archive_s3_uri, batch_size, selected_phases, mode, metrics, reporter)
    )

    print_migration_report(report)
    if has_migration_failures(report):
        error("Migration finished with failed rows. See the audit table for details.")
        raise click.Abort()
    success(f"Migration ({mode}) finished cleanly.")


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
                    raise _DryRunDiscard()
    except _DryRunDiscard:
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


def print_migration_report(report: dict[str, Any]):
    console().rule("[bold]Migration report")
    console().print_json(json.dumps(report, default=str, indent=2))


def invoke_from_lambda(
    rails_url: str, archive_s3_uri: str | None, *, dry_run: bool, phases: str = ",".join(ALL_PHASES)
):
    """Programmatic entry point so the Lambda wrapper can invoke without going through Click."""
    env_default = os.environ.get("MITUPBOT_ENV", Env.PROD.value)
    selected_phases = tuple(p.strip() for p in phases.split(",") if p.strip())
    mode = MigrationMode.DRY_RUN if dry_run else MigrationMode.LIVE

    config = Config.from_providers(EnvVariablesConfigProvider(), TomlConfigProvider(env=Env(env_default)))
    config.db.engine_echo = False
    configure_logging(OutputMode.LOG)
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
