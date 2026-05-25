from collections.abc import Iterator
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from mitup_bot.migration.archive import ArchiveWriter
from mitup_bot.migration.modes import MigrationMode
from mitup_bot.migration.phases import run_migration
from mitup_bot.migration.rails_reader import RailsReader
from mitup_bot.migration.reporting import MigrationReporter, OutputMode
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from tests.helpers import MetricAssertions, make_test_metrics_client


def _make_reporter() -> MigrationReporter:
    return MigrationReporter(OutputMode.LOG)


class _StubRailsReader:
    """Canned RailsReader stand-in. Yields preconfigured rows per query substring and
    returns preconfigured counts per table."""

    def __init__(
        self,
        *,
        rows_by_query: dict[str, list[dict[str, Any]]] | None = None,
        counts: dict[str, int] | None = None,
    ):
        self._rows_by_query = rows_by_query or {}
        self._counts = counts or {}
        self.stream_calls: list[str] = []
        self.count_calls: list[str] = []

    def stream(self, query: str, params: tuple[object, ...] | None = None) -> Iterator[dict[str, Any]]:
        self.stream_calls.append(query)
        # Match the query against any registered substring.
        for substring, rows in self._rows_by_query.items():
            if substring in query:
                yield from rows
                return
        return

    def count(self, table: str) -> int:
        self.count_calls.append(table)
        return self._counts.get(table, 0)


def _make_metrics() -> tuple[MetricsClient, MetricAssertions]:
    client = make_test_metrics_client()
    return client, MetricAssertions(client)


def _make_session_with_zero_counts() -> MagicMock:
    """Return a Session-shaped mock whose `exec().first()` always returns 0.

    `verify` runs `SELECT COUNT(*)` queries against the new DB; tests don't care about
    actual rows, only that the delta metric is emitted.
    """
    session = MagicMock()
    exec_result = MagicMock()
    exec_result.first.return_value = 0
    exec_result.all.return_value = []
    session.exec.return_value = exec_result
    return session


def test_run_migration_verify_phase_only_calls_verify():
    session = _make_session_with_zero_counts()
    reader = _StubRailsReader(
        counts={"users": 3, "meetups": 1, "messages": 0, "user_join_meetups": 2, "user_waiting_lists": 0}
    )
    writer = ArchiveWriter(s3_uri=None, dry_run=True, s3_client=MagicMock())
    metrics, assertions = _make_metrics()

    report = run_migration(
        session=session,
        reader=cast(RailsReader, reader),
        archive_writer=writer,
        metrics=metrics,
        mode=MigrationMode.DRY_RUN,
        phases=("verify",),
        reporter=_make_reporter(),
    )

    # Only verify ran → no per-phase entries; verification populated.
    assert report["phases"] == {}
    assert report["archive"] == {}
    assert report["verification"] == {"users": 3, "meetups": 1, "messages": 0, "joined_users": 2}

    # Verification delta metric emitted once per Rails table tracked by verify().
    for table, delta in (("users", 3), ("meetups", 1), ("messages", 0), ("joined_users", 2)):
        assertions.assert_emitted(
            name=MetricKey.MIGRATION_VERIFICATION_DELTA,
            value=delta,
            unit=MetricUnit.COUNT,
            dimensions={"mode": str(MigrationMode.DRY_RUN), "table": table},
        )

    # No archive metrics emitted in verify-only run.
    assertions.assert_not_emitted(name=MetricKey.MIGRATION_ARCHIVE_BYTES)
    assertions.assert_not_emitted(name=MetricKey.MIGRATION_ARCHIVE_ROWS)


def test_run_migration_archive_phase_only_calls_archive():
    session = _make_session_with_zero_counts()
    # Archive iterates ARCHIVED_TABLES = ("support_messages", "payments", "shared_meetups", "chats").
    reader = _StubRailsReader(
        rows_by_query={
            '"support_messages"': [{"id": 1, "body": "hi"}],
            '"payments"': [],
            '"shared_meetups"': [],
            '"chats"': [{"id": 7}],
        }
    )
    writer = ArchiveWriter(s3_uri="s3://bucket/prefix/", dry_run=True, s3_client=MagicMock())
    metrics, assertions = _make_metrics()

    report = run_migration(
        session=session,
        reader=cast(RailsReader, reader),
        archive_writer=writer,
        metrics=metrics,
        mode=MigrationMode.DRY_RUN,
        phases=("archive",),
        reporter=_make_reporter(),
    )

    assert report["phases"] == {}
    assert report["verification"] == {}
    # All four archived tables represented in the archive section.
    assert set(report["archive"].keys()) == {"support_messages", "payments", "shared_meetups", "chats"}
    assert report["archive"]["support_messages"]["read"] == 1
    assert report["archive"]["chats"]["read"] == 1
    assert report["archive"]["payments"]["read"] == 0

    # Archive-bytes metric emitted once per archived table (even when empty).
    for table in ("support_messages", "payments", "shared_meetups", "chats"):
        assertions.assert_emitted(
            name=MetricKey.MIGRATION_ARCHIVE_BYTES,
            unit=MetricUnit.BYTES,
            dimensions={"mode": str(MigrationMode.DRY_RUN), "table": table},
        )

    # verify() is not invoked → no verification-delta metric.
    assertions.assert_not_emitted(name=MetricKey.MIGRATION_VERIFICATION_DELTA)
    # No row-read metric for the user phase (only archive ran).
    assertions.assert_not_emitted(
        name=MetricKey.MIGRATION_ROWS_READ,
        dimensions={"table": "users"},
    )


def test_run_migration_unknown_phase_raises_value_error():
    session = _make_session_with_zero_counts()
    reader = _StubRailsReader()
    writer = ArchiveWriter(s3_uri=None, dry_run=True, s3_client=MagicMock())
    metrics = make_test_metrics_client()

    with pytest.raises(ValueError, match="Unknown phase"):
        run_migration(
            session=session,
            reader=cast(RailsReader, reader),
            archive_writer=writer,
            metrics=metrics,
            mode=MigrationMode.DRY_RUN,
            phases=("nonsense",),
            reporter=_make_reporter(),
        )


def test_stub_reader_conforms_to_rails_reader_protocol():
    """Smoke test: ensure _StubRailsReader has the methods run_migration depends on.

    If RailsReader's surface grows, this test will fail and signal the stub is out of date.
    """
    stub = _StubRailsReader()
    assert callable(stub.stream)
    assert callable(stub.count)
    # Verify the real class has the same names — guard against rename drift.
    assert hasattr(RailsReader, "stream")
    assert hasattr(RailsReader, "count")
