from unittest import mock

import sqlalchemy as sa
from structlog.testing import capture_logs

from mitup_bot.migrations import helpers

REVISION = "52824dd9ee6b"
UPDATE_SQL = "UPDATE meetups SET title = title_tagged WHERE title_tagged IS NOT NULL"


def test_execute_bulk_reports_the_revision_and_row_count():
    """The row count is the whole point: without it a statement that matched nothing and one that
    rewrote every row are indistinguishable once the migration has run."""
    bind = mock.MagicMock(name="connection")
    bind.execute.return_value = mock.MagicMock(rowcount=4213)

    with capture_logs() as logs:
        with mock.patch.object(helpers.op, "get_bind", return_value=bind):
            rows = helpers.execute_bulk(REVISION, "flip_tagged_titles", UPDATE_SQL)

    assert rows == 4213
    executed = bind.execute.call_args.args[0]
    assert isinstance(executed, sa.TextClause)
    assert str(executed) == UPDATE_SQL
    mutation_logs = [entry for entry in logs if entry["event"] == "Migration data mutation"]
    assert len(mutation_logs) == 1
    entry = mutation_logs[0]
    assert entry["revision"] == REVISION
    assert entry["statement"] == "flip_tagged_titles"
    assert entry["rows"] == 4213


def test_execute_bulk_reports_no_row_count_when_alembic_only_renders_the_statement():
    """Offline mode writes the statement into a script and executes nothing, so `None` is the
    truthful answer — reporting 0 would claim the statement ran and matched nothing."""
    bind = mock.MagicMock(name="connection")
    bind.execute.return_value = None

    with capture_logs() as logs:
        with mock.patch.object(helpers.op, "get_bind", return_value=bind):
            rows = helpers.execute_bulk(REVISION, "flip_tagged_titles", UPDATE_SQL)

    assert rows is None
    assert [entry["rows"] for entry in logs if entry["event"] == "Migration data mutation"] == [None]
