"""Persistent old_id → new_id mapping used to make the migration idempotent and resumable."""

from enum import StrEnum

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession


class AuditStatus(StrEnum):
    INSERTED = "inserted"
    SKIPPED = "skipped"
    FAILED = "failed"
    ARCHIVED = "archived"


_AUDIT_TABLE = "migration_audit"


class AuditStore:
    """Thin SQL wrapper around the `migration_audit` table.

    The table is intentionally narrow so we never need to evolve it during cutover; it stores
    the mapping from a Rails primary key to the new-schema primary key plus a status string.

    Statements run on the session's transaction connection (same transaction scope): SQLModel
    deprecates `Session.execute` in favor of `exec()`, which is typed only for SQLModel selects,
    not the raw `text()` SQL used here.
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def existing_mapping(self, table: str) -> dict[int, int]:
        """Return every `old_id → new_id` mapping previously recorded for `table`.

        Only rows with status='inserted' and a non-null new_id participate. Failed and
        skipped rows are deliberately not in the returned mapping so a re-run can retry them.
        """
        result = await (await self._session.connection()).execute(
            text(
                f"SELECT old_id, new_id FROM {_AUDIT_TABLE} "  # noqa: S608 — table name constant
                "WHERE table_name = :table AND status = :status AND new_id IS NOT NULL"
            ),
            {"table": table, "status": AuditStatus.INSERTED.value},
        )
        return {int(old): int(new) for old, new in result.all()}

    async def processed_old_ids(self, table: str) -> set[int]:
        """Return every old_id we have seen for `table`, regardless of status.

        Used to skip rows that we have already considered (whether inserted, skipped, or
        failed) so a re-run only retries previously-failed rows when explicitly asked.
        """
        result = await (await self._session.connection()).execute(
            text(f"SELECT old_id FROM {_AUDIT_TABLE} WHERE table_name = :table"),  # noqa: S608 — constant
            {"table": table},
        )
        return {int(row[0]) for row in result.all()}

    async def record(
        self, table: str, old_id: int, *, new_id: int | None, status: AuditStatus, note: str | None = None
    ):
        await (await self._session.connection()).execute(
            text(
                f"INSERT INTO {_AUDIT_TABLE} (table_name, old_id, new_id, status, note) "  # noqa: S608 — constant
                "VALUES (:table, :old_id, :new_id, :status, :note) "
                "ON CONFLICT (table_name, old_id) DO UPDATE SET "
                "new_id = EXCLUDED.new_id, status = EXCLUDED.status, note = EXCLUDED.note"
            ),
            {
                "table": table,
                "old_id": old_id,
                "new_id": new_id,
                "status": status.value,
                "note": note,
            },
        )

    async def count(self, table: str, status: AuditStatus | None = None) -> int:
        if status is None:
            sql = f"SELECT COUNT(*) FROM {_AUDIT_TABLE} WHERE table_name = :table"  # noqa: S608 — constant
            params: dict[str, object] = {"table": table}
        else:
            sql = (
                f"SELECT COUNT(*) FROM {_AUDIT_TABLE} "  # noqa: S608 — constant
                "WHERE table_name = :table AND status = :status"
            )
            params = {"table": table, "status": status.value}
        result = await (await self._session.connection()).execute(text(sql), params)
        return int(result.scalar() or 0)
