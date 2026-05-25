import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func
from sqlmodel import Session, select

from mitup_bot.migration.archive import ArchiveWriter
from mitup_bot.migration.audit import AuditStatus, AuditStore
from mitup_bot.migration.mappers import (
    RowMappingError,
    map_invitation,
    map_join,
    map_meetup,
    map_message,
    map_user_and_settings,
)
from mitup_bot.migration.modes import MigrationMode
from mitup_bot.migration.rails_reader import RailsReader
from mitup_bot.migration.reporting import MigrationReporter
from mitup_bot.models import JoinedUsers, Meetup, Message, Settings, User
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit

ARCHIVED_TABLES: tuple[str, ...] = ("support_messages", "payments", "shared_meetups", "chats")


@dataclass
class PhaseSummary:
    table: str
    read: int = 0
    inserted: int = 0
    skipped: int = 0
    failed: int = 0
    duration_ms: int = 0
    extra: dict[str, int] = field(default_factory=dict)


def emit_count(metrics: MetricsClient, key: MetricKey, n: int, *, dims: dict[str, str]):
    metrics.emit(key, n, MetricUnit.COUNT, dimensions=dims)


def phase_dims(mode: MigrationMode, table: str) -> dict[str, str]:
    return {"mode": str(mode), "table": table}


def record_row_inserted(
    metrics: MetricsClient,
    mode: MigrationMode,
    table: str,
    audit: AuditStore,
    reporter: MigrationReporter,
    old_id: int,
    new_id: int,
):
    audit.record(table, old_id, new_id=new_id, status=AuditStatus.INSERTED)
    emit_count(metrics, MetricKey.MIGRATION_ROWS_INSERTED, 1, dims=phase_dims(mode, table))
    reporter.row_inserted(table, old_id, new_id)


def record_row_skipped(
    metrics: MetricsClient,
    mode: MigrationMode,
    table: str,
    audit: AuditStore,
    reporter: MigrationReporter,
    old_id: int,
    reason: str,
):
    audit.record(table, old_id, new_id=None, status=AuditStatus.SKIPPED, note=reason)
    emit_count(metrics, MetricKey.MIGRATION_ROWS_SKIPPED, 1, dims={**phase_dims(mode, table), "reason": reason})
    reporter.row_skipped(table, old_id, reason)


def record_row_failed(
    metrics: MetricsClient,
    mode: MigrationMode,
    table: str,
    audit: AuditStore,
    reporter: MigrationReporter,
    old_id: int,
    error: str,
):
    audit.record(table, old_id, new_id=None, status=AuditStatus.FAILED, note=error)
    emit_count(metrics, MetricKey.MIGRATION_ROWS_FAILED, 1, dims=phase_dims(mode, table))
    reporter.row_failed(table, old_id, error)


def emit_phase_duration(metrics: MetricsClient, mode: MigrationMode, phase: str, duration_ms: int):
    metrics.emit(
        MetricKey.MIGRATION_PHASE_DURATION_MS,
        duration_ms,
        MetricUnit.MILLISECONDS,
        dimensions={"mode": str(mode), "phase": phase},
    )


def migrate_users(
    *,
    session: Session,
    reader: RailsReader,
    audit: AuditStore,
    metrics: MetricsClient,
    mode: MigrationMode,
    reporter: MigrationReporter,
) -> PhaseSummary:
    summary = PhaseSummary(table="users")
    start = time.perf_counter()
    reporter.phase_start("users", "users")
    already = audit.processed_old_ids("users")

    for row in reader.stream("SELECT * FROM users ORDER BY id"):
        summary.read += 1
        emit_count(metrics, MetricKey.MIGRATION_ROWS_READ, 1, dims=phase_dims(mode, "users"))
        old_id = int(row["id"])
        if old_id in already:
            summary.skipped += 1
            continue
        try:
            user_kwargs, settings_kwargs = map_user_and_settings(row)
        except RowMappingError as exc:
            summary.skipped += 1
            record_row_skipped(metrics, mode, "users", audit, reporter, old_id, str(exc))
            continue
        user = User(**user_kwargs)
        try:
            with session.begin_nested():
                session.add(user)
                session.flush()
                settings = Settings(user_id=user.id, **settings_kwargs)
                session.add(settings)
        except Exception as exc:  # noqa: BLE001 — record & continue
            summary.failed += 1
            record_row_failed(metrics, mode, "users", audit, reporter, old_id, repr(exc))
            continue
        summary.inserted += 1
        record_row_inserted(metrics, mode, "users", audit, reporter, old_id, int(user.id or 0))

    summary.duration_ms = int((time.perf_counter() - start) * 1000)
    emit_phase_duration(metrics, mode, "users", summary.duration_ms)
    reporter.phase_end(summary)
    return summary


def migrate_meetups(
    *,
    session: Session,
    reader: RailsReader,
    audit: AuditStore,
    metrics: MetricsClient,
    mode: MigrationMode,
    reporter: MigrationReporter,
) -> PhaseSummary:
    summary = PhaseSummary(table="meetups")
    start = time.perf_counter()
    reporter.phase_start("meetups", "meetups")
    user_map = audit.existing_mapping("users")
    already = audit.processed_old_ids("meetups")

    for row in reader.stream("SELECT * FROM meetups ORDER BY id"):
        summary.read += 1
        emit_count(metrics, MetricKey.MIGRATION_ROWS_READ, 1, dims=phase_dims(mode, "meetups"))
        old_id = int(row["id"])
        if old_id in already:
            summary.skipped += 1
            continue
        owner_old_id = row.get("owner_id")
        owner_new_id = user_map.get(int(owner_old_id)) if owner_old_id is not None else None
        if owner_new_id is None:
            summary.skipped += 1
            record_row_skipped(metrics, mode, "meetups", audit, reporter, old_id, "owner-not-migrated")
            continue
        try:
            meetup_kwargs = map_meetup(row, owner_new_id=owner_new_id)
        except RowMappingError as exc:
            summary.skipped += 1
            record_row_skipped(metrics, mode, "meetups", audit, reporter, old_id, str(exc))
            continue
        meetup = Meetup(**meetup_kwargs)
        try:
            with session.begin_nested():
                session.add(meetup)
        except Exception as exc:  # noqa: BLE001
            summary.failed += 1
            record_row_failed(metrics, mode, "meetups", audit, reporter, old_id, repr(exc))
            continue
        summary.inserted += 1
        record_row_inserted(metrics, mode, "meetups", audit, reporter, old_id, int(meetup.id or 0))

    summary.duration_ms = int((time.perf_counter() - start) * 1000)
    emit_phase_duration(metrics, mode, "meetups", summary.duration_ms)
    reporter.phase_end(summary)
    return summary


def pending_notifications(reader: RailsReader) -> set[tuple[int, int]]:
    """Return the set of (user_id, meetup_id) pairs that still have a Rails `notifications` row."""
    pending: set[tuple[int, int]] = set()
    for row in reader.stream("SELECT user_id, meetup_id FROM notifications"):
        u, m = row.get("user_id"), row.get("meetup_id")
        if u is not None and m is not None:
            pending.add((int(u), int(m)))
    return pending


def migrate_joins(
    *,
    session: Session,
    reader: RailsReader,
    audit: AuditStore,
    metrics: MetricsClient,
    mode: MigrationMode,
    reporter: MigrationReporter,
) -> PhaseSummary:
    summary = PhaseSummary(table="joined_users")
    start = time.perf_counter()
    reporter.phase_start("joins", "joined_users")
    user_map = audit.existing_mapping("users")
    meetup_map = audit.existing_mapping("meetups")
    pending = pending_notifications(reader)
    seen_pairs: set[tuple[int, int]] = set()

    # user_join_meetups wins over user_waiting_lists when the same (user, meeting) pair appears in both.
    sources = (
        ("user_join_meetups", False),
        ("user_waiting_lists", True),
    )

    for source_table, is_waiting_list in sources:
        already = audit.processed_old_ids(source_table)
        for row in reader.stream(f"SELECT * FROM {source_table} ORDER BY id"):  # noqa: S608 — constant
            summary.read += 1
            emit_count(metrics, MetricKey.MIGRATION_ROWS_READ, 1, dims=phase_dims(mode, source_table))
            old_id = int(row["id"])
            if old_id in already:
                summary.skipped += 1
                continue

            user_old, meetup_old = row.get("user_id"), row.get("meetup_id")
            if user_old is None or meetup_old is None:
                summary.skipped += 1
                record_row_skipped(metrics, mode, source_table, audit, reporter, old_id, "null-fk")
                continue
            user_new = user_map.get(int(user_old))
            meetup_new = meetup_map.get(int(meetup_old))
            if user_new is None or meetup_new is None:
                summary.skipped += 1
                record_row_skipped(metrics, mode, source_table, audit, reporter, old_id, "fk-not-migrated")
                continue
            pair = (user_new, meetup_new)
            if pair in seen_pairs:
                summary.skipped += 1
                record_row_skipped(metrics, mode, source_table, audit, reporter, old_id, "duplicate-pair")
                continue

            join_kwargs = map_join(
                row,
                user_new_id=user_new,
                meetup_new_id=meetup_new,
                is_waiting_list=is_waiting_list,
                notification_pending=(int(user_old), int(meetup_old)) in pending,
            )
            join = JoinedUsers(**join_kwargs)
            try:
                with session.begin_nested():
                    session.add(join)
            except Exception as exc:  # noqa: BLE001
                summary.failed += 1
                record_row_failed(metrics, mode, source_table, audit, reporter, old_id, repr(exc))
                continue
            seen_pairs.add(pair)
            summary.inserted += 1
            record_row_inserted(metrics, mode, source_table, audit, reporter, old_id, int(join.id or 0))

    summary.duration_ms = int((time.perf_counter() - start) * 1000)
    emit_phase_duration(metrics, mode, "joined_users", summary.duration_ms)
    reporter.phase_end(summary)
    return summary


def migrate_invitations(
    *,
    session: Session,
    reader: RailsReader,
    audit: AuditStore,
    metrics: MetricsClient,
    mode: MigrationMode,
    reporter: MigrationReporter,
) -> PhaseSummary:
    summary = PhaseSummary(table="invitations")
    start = time.perf_counter()
    reporter.phase_start("invitations", "invitations")
    user_map = audit.existing_mapping("users")
    meetup_map = audit.existing_mapping("meetups")
    already = audit.processed_old_ids("invitations")

    for row in reader.stream("SELECT * FROM invitations ORDER BY id"):
        summary.read += 1
        emit_count(metrics, MetricKey.MIGRATION_ROWS_READ, 1, dims=phase_dims(mode, "invitations"))
        old_id = int(row["id"])
        if old_id in already:
            summary.skipped += 1
            continue

        friend_old, host_old, meetup_old = row.get("friend_id"), row.get("host_id"), row.get("meetup_id")
        if friend_old is None or host_old is None or meetup_old is None:
            summary.skipped += 1
            record_row_skipped(metrics, mode, "invitations", audit, reporter, old_id, "null-fk")
            continue
        friend_new = user_map.get(int(friend_old))
        host_new = user_map.get(int(host_old))
        meetup_new = meetup_map.get(int(meetup_old))
        if friend_new is None or host_new is None or meetup_new is None:
            summary.skipped += 1
            record_row_skipped(metrics, mode, "invitations", audit, reporter, old_id, "phantom-or-not-migrated")
            continue

        join = session.exec(
            select(JoinedUsers).where((JoinedUsers.user_id == friend_new) & (JoinedUsers.meetup_id == meetup_new))
        ).first()
        if join is None:
            summary.skipped += 1
            record_row_skipped(metrics, mode, "invitations", audit, reporter, old_id, "no-joined-link")
            continue

        try:
            with session.begin_nested():
                for key, value in map_invitation(host_new).items():
                    setattr(join, key, value)
                session.add(join)
        except Exception as exc:  # noqa: BLE001
            summary.failed += 1
            record_row_failed(metrics, mode, "invitations", audit, reporter, old_id, repr(exc))
            continue
        summary.inserted += 1
        record_row_inserted(metrics, mode, "invitations", audit, reporter, old_id, int(join.id or 0))

    summary.duration_ms = int((time.perf_counter() - start) * 1000)
    emit_phase_duration(metrics, mode, "invitations", summary.duration_ms)
    reporter.phase_end(summary)
    return summary


def migrate_messages(
    *,
    session: Session,
    reader: RailsReader,
    audit: AuditStore,
    metrics: MetricsClient,
    mode: MigrationMode,
    reporter: MigrationReporter,
) -> PhaseSummary:
    summary = PhaseSummary(table="messages")
    start = time.perf_counter()
    reporter.phase_start("messages", "messages")
    meetup_map = audit.existing_mapping("meetups")
    already = audit.processed_old_ids("messages")

    for row in reader.stream("SELECT * FROM messages ORDER BY id"):
        summary.read += 1
        emit_count(metrics, MetricKey.MIGRATION_ROWS_READ, 1, dims=phase_dims(mode, "messages"))
        old_id = int(row["id"])
        if old_id in already:
            summary.skipped += 1
            continue
        meetup_old = row.get("meetup_id")
        meetup_new = meetup_map.get(int(meetup_old)) if meetup_old is not None else None
        if meetup_new is None:
            summary.skipped += 1
            record_row_skipped(metrics, mode, "messages", audit, reporter, old_id, "meetup-not-migrated")
            continue
        try:
            message_kwargs = map_message(row, meetup_new_id=meetup_new)
        except RowMappingError as exc:
            summary.skipped += 1
            record_row_skipped(metrics, mode, "messages", audit, reporter, old_id, str(exc))
            continue
        message = Message(**message_kwargs)
        try:
            with session.begin_nested():
                session.add(message)
        except Exception as exc:  # noqa: BLE001
            summary.failed += 1
            record_row_failed(metrics, mode, "messages", audit, reporter, old_id, repr(exc))
            continue
        summary.inserted += 1
        record_row_inserted(metrics, mode, "messages", audit, reporter, old_id, int(message.id or 0))

    summary.duration_ms = int((time.perf_counter() - start) * 1000)
    emit_phase_duration(metrics, mode, "messages", summary.duration_ms)
    reporter.phase_end(summary)
    return summary


def archive_dropped_tables(
    *,
    reader: RailsReader,
    writer: ArchiveWriter,
    metrics: MetricsClient,
    mode: MigrationMode,
    reporter: MigrationReporter,
) -> dict[str, PhaseSummary]:
    summaries: dict[str, PhaseSummary] = {}
    reporter.phase_start("archive", "dropped tables")
    for table in ARCHIVED_TABLES:
        start = time.perf_counter()
        summary = PhaseSummary(table=table)
        rows = reader.stream(f'SELECT * FROM "{table}"')  # noqa: S608 — constant
        row_count, byte_count = writer.write(table, rows)
        summary.read = row_count
        summary.inserted = row_count
        summary.extra["bytes"] = byte_count
        summary.duration_ms = int((time.perf_counter() - start) * 1000)
        emit_count(metrics, MetricKey.MIGRATION_ROWS_READ, row_count, dims=phase_dims(mode, table))
        emit_count(metrics, MetricKey.MIGRATION_ARCHIVE_ROWS, row_count, dims=phase_dims(mode, table))
        metrics.emit(
            MetricKey.MIGRATION_ARCHIVE_BYTES,
            byte_count,
            MetricUnit.BYTES,
            dimensions=phase_dims(mode, table),
        )
        emit_phase_duration(metrics, mode, f"archive:{table}", summary.duration_ms)
        reporter.archive_table(table, row_count, byte_count)
        summaries[table] = summary
    return summaries


def verify(
    *,
    session: Session,
    reader: RailsReader,
    metrics: MetricsClient,
    mode: MigrationMode,
) -> dict[str, int]:
    """Emit `MIGRATION_VERIFICATION_DELTA` per table — Rails count minus new-DB count."""
    deltas: dict[str, int] = {}
    pairs = (
        ("users", User),
        ("meetups", Meetup),
        ("messages", Message),
    )
    for rails_table, model in pairs:
        rails_count = reader.count(rails_table)
        new_count = session.exec(select(func.count()).select_from(model)).first() or 0
        delta = rails_count - int(new_count)
        deltas[rails_table] = delta
        metrics.emit(
            MetricKey.MIGRATION_VERIFICATION_DELTA,
            delta,
            MetricUnit.COUNT,
            dimensions=phase_dims(mode, rails_table),
        )
    # joined_users delta uses the sum of both Rails join tables.
    rails_joins = reader.count("user_join_meetups") + reader.count("user_waiting_lists")
    new_joins = session.exec(select(func.count()).select_from(JoinedUsers)).first() or 0
    deltas["joined_users"] = rails_joins - int(new_joins)
    metrics.emit(
        MetricKey.MIGRATION_VERIFICATION_DELTA,
        deltas["joined_users"],
        MetricUnit.COUNT,
        dimensions=phase_dims(mode, "joined_users"),
    )
    return deltas


def run_migration(
    *,
    session: Session,
    reader: RailsReader,
    archive_writer: ArchiveWriter,
    metrics: MetricsClient,
    mode: MigrationMode,
    phases: tuple[str, ...],
    reporter: MigrationReporter,
) -> dict[str, Any]:
    """Run the selected phases in order and return a report dict.

    All phases run inside a single outer transaction owned by the caller. In live mode the
    caller's context manager commits on clean exit; in dry-run mode the caller raises a
    sentinel exception to force rollback. Per-row failures are isolated via SAVEPOINTs
    (`session.begin_nested()`), so a bad row never aborts the surrounding transaction.
    """
    audit = AuditStore(session)
    report: dict[str, Any] = {"mode": str(mode), "phases": {}, "verification": {}, "archive": {}}

    dispatch = {
        "users": lambda: migrate_users(
            session=session, reader=reader, audit=audit, metrics=metrics, mode=mode, reporter=reporter
        ),
        "meetups": lambda: migrate_meetups(
            session=session, reader=reader, audit=audit, metrics=metrics, mode=mode, reporter=reporter
        ),
        "joins": lambda: migrate_joins(
            session=session, reader=reader, audit=audit, metrics=metrics, mode=mode, reporter=reporter
        ),
        "invitations": lambda: migrate_invitations(
            session=session, reader=reader, audit=audit, metrics=metrics, mode=mode, reporter=reporter
        ),
        "messages": lambda: migrate_messages(
            session=session, reader=reader, audit=audit, metrics=metrics, mode=mode, reporter=reporter
        ),
    }

    for phase in phases:
        match phase:
            case "users" | "meetups" | "joins" | "invitations" | "messages":
                summary = dispatch[phase]()
                report["phases"][phase] = summary_dict(summary)
            case "archive":
                archived = archive_dropped_tables(
                    reader=reader, writer=archive_writer, metrics=metrics, mode=mode, reporter=reporter
                )
                report["archive"] = {name: summary_dict(s) for name, s in archived.items()}
            case "verify":
                deltas = verify(session=session, reader=reader, metrics=metrics, mode=mode)
                reporter.verification(deltas)
                report["verification"] = deltas
            case _:
                raise ValueError(f"Unknown phase: {phase!r}")

    return report


def summary_dict(summary: PhaseSummary) -> dict[str, Any]:
    return {
        "table": summary.table,
        "read": summary.read,
        "inserted": summary.inserted,
        "skipped": summary.skipped,
        "failed": summary.failed,
        "duration_ms": summary.duration_ms,
        **summary.extra,
    }
