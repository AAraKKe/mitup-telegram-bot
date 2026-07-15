import asyncio
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

import click
import psycopg
import sqlalchemy.exc
from psycopg.conninfo import conninfo_to_dict

from mitup_bot import db
from mitup_bot.config import Config, DbConfig, Env, EnvVariablesConfigProvider, TomlConfigProvider
from mitup_bot.migration import (
    ALL_PHASES,
    MetricsFlusher,
    MigrationMode,
    MigrationReporter,
    OutputMode,
    configure_migration_logging,
    has_migration_failures,
    run_pipeline_then_flush,
)
from mitup_bot.monitoring import EmfBackend, MetricsBackend, MetricsClient, NullBackend, configure_emf_backend

from .console import console, error, success

log = logging.getLogger("mitup_bot.migration")


@click.group()
def cli():
    """Legacy Rails → new-schema migration tooling."""


@cli.command()
@click.option(
    "--env",
    default=Env.PROD,
    type=click.Choice(choices=Env, case_sensitive=False),
    help="Which environment's config supplies the target-DB connection (the new-schema Postgres the run "
    "writes into). MITUPBOT__DB__* env vars still override individual settings. Defaults to prod.",
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
@click.option(
    "--connect-timeout",
    default=10,
    type=int,
    help="Seconds to wait for the Rails DB connection before failing fast. The Rails endpoint is often "
    "only reachable from inside the VPC, so a short timeout turns an unreachable host into a clear error "
    "instead of a silent hang.",
)
@click.option(
    "--metrics/--no-metrics",
    default=False,
    help="Emit CloudWatch EMF metrics for the run. Off by default: the EMF lines add a lot of noise to "
    "the console. Pass --metrics to record per-phase progress on dashboards.",
)
def migrate(
    env: Env,
    rails_url: str,
    archive_s3_uri: str | None,
    batch_size: int,
    phases: str,
    dry_run: bool,
    output: OutputMode,
    metrics_flush_every: int,
    connect_timeout: int,
    metrics: bool,
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
    configure_migration_logging(output)
    log_startup_diagnostics(
        env, mode, selected_phases, output, metrics_flush_every, archive_s3_uri, connect_timeout, config.db, rails_url
    )
    db.configure_db(config.db, skip_if_initialized=True)

    if metrics:
        configure_emf_backend(config.metrics)
        backend: MetricsBackend = EmfBackend(base_dimensions={"Tool": "MigrateFromRails"})
    else:
        backend = NullBackend()
    metrics_client = MetricsClient(backend=backend)
    flusher = MetricsFlusher(metrics_client, flush_every=metrics_flush_every)
    reporter = MigrationReporter(
        output,
        console=console() if output is OutputMode.CONSOLE else None,
        flusher=flusher,
    )

    if mode is MigrationMode.LIVE and "archive" in selected_phases and not archive_s3_uri:
        error("--archive-s3-uri / MIGRATE_ARCHIVE_S3_URI is required when running the archive phase live.")
        raise click.Abort()

    try:
        report = asyncio.run(
            run_pipeline_then_flush(
                rails_url, archive_s3_uri, batch_size, selected_phases, mode, metrics_client, reporter, connect_timeout
            )
        )
    except psycopg.OperationalError as exc:
        rails = rails_connection_target(rails_url)
        error(
            f"Could not connect to the Rails DB at {rails.get('host')}:{rails.get('port')} ({exc}). "
            "The endpoint may only be reachable from inside the VPC."
        )
        raise click.Abort() from exc
    except sqlalchemy.exc.OperationalError as exc:
        error(
            f"Could not communicate with the target DB at {config.db.url}:{config.db.port} ({exc}). "
            "The tunnel/endpoint may not be forwarding to it."
        )
        raise click.Abort() from exc

    print_migration_report(report)
    if has_migration_failures(report):
        error("Migration finished with failed rows. See the audit table for details.")
        raise click.Abort()
    success(f"Migration ({mode}) finished cleanly.")


@cli.command()
@click.option(
    "--backup-file",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to the legacy Rails pg_dump in custom format (the Heroku download).",
)
@click.option(
    "--service",
    default="heroku-postgres",
    help="docker compose service name of the throwaway Postgres container to restore into.",
)
@click.option("--db", "database", default="rails", help="Database inside the container to restore into.")
@click.option("--user", "db_user", default="mitupbot", help="Postgres role inside the container.")
def restore(backup_file: Path, service: str, database: str, db_user: str):
    """Restore a legacy Rails custom-format pg_dump into the throwaway container database.

    The host pg_restore is often older than the dump's archive version, so pg_restore runs inside the
    container via `docker compose exec`, with the dump streamed over stdin. Heroku role ownership and
    privileges are dropped, and existing objects are dropped first (--clean --if-exists) so re-running
    the restore into the same container starts from a clean slate instead of erroring on duplicates.
    Run from the repository root (so docker compose finds docker-compose.yaml) with the target
    container already up.
    """
    command = [
        "docker",
        "compose",
        "exec",
        "-T",
        service,
        "pg_restore",
        "--no-owner",
        "--no-privileges",
        "--clean",
        "--if-exists",
        "--dbname",
        database,
        "--username",
        db_user,
    ]
    console().print(f"Restoring [bold]{backup_file}[/] into service '{service}' database '{database}'…")
    try:
        with backup_file.open("rb") as dump:
            result = subprocess.run(command, stdin=dump, check=False)
    except FileNotFoundError as exc:
        error("`docker` was not found on PATH. Install Docker and run this from the repository root.")
        raise click.Abort() from exc

    if result.returncode == 0:
        success(f"Restored {backup_file.name} into service '{service}' database '{database}'.")
        return
    console().print(
        f"[yellow]⚠ pg_restore exited with code {result.returncode}.[/] For a Heroku dump this is usually "
        "harmless — ALTER DATABASE, role, and heroku_ext lines fail because those objects don't exist "
        "locally. Confirm the tables and row counts landed before migrating; empty tables mean the "
        "container was not up or the database was missing."
    )


def rails_connection_target(dsn: str) -> dict[str, str | int | None]:
    """Parse the Rails DSN into safe-to-log connection fields — never the password."""
    parsed = conninfo_to_dict(dsn)
    return {key: parsed.get(key) for key in ("host", "port", "dbname", "user")}


def log_startup_diagnostics(
    env: Env,
    mode: MigrationMode,
    phases: tuple[str, ...],
    output: OutputMode,
    metrics_flush_every: int,
    archive_s3_uri: str | None,
    connect_timeout: int,
    db_config: DbConfig,
    rails_url: str,
):
    log.info("Environment: %s", env)
    log.info("Mode: %s", mode)
    log.info("Phases: %s", ", ".join(phases))
    log.info("Output mode: %s", output)
    log.info("Metrics flush every: %d rows", metrics_flush_every)
    log.info("Rails connect timeout: %ds", connect_timeout)
    log.info("Archive S3 URI: %s", archive_s3_uri or "(not set)")
    log.info("Target DB: host=%s port=%d database=%s", db_config.url, db_config.port, db_config.database)
    rails = rails_connection_target(rails_url)
    log.info(
        "Rails DB: host=%s port=%s dbname=%s user=%s",
        rails.get("host"),
        rails.get("port"),
        rails.get("dbname"),
        rails.get("user"),
    )


def print_migration_report(report: dict[str, Any]):
    console().rule("[bold]Migration report")
    console().print_json(json.dumps(report, default=str, indent=2))


def main():
    cli()


if __name__ == "__main__":
    main()
