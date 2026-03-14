import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlmodel import Session

pytestmark = pytest.mark.db_test


def _alembic_head() -> str:
    cfg = Config("alembic.ini")
    head = ScriptDirectory.from_config(cfg).get_current_head()
    if head is None:
        raise RuntimeError("No Alembic revisions found")
    return head


def test_no_pending_migrations(db_session: Session) -> None:
    """Verify the DB is at the current Alembic head revision.

    If this test fails, the DB has not had all migrations applied.
    Run `alembic upgrade head` to bring it up to date.
    """
    expected = _alembic_head()
    rows = (
        db_session.exec(  # type: ignore[call-overload]
            text("SELECT version_num FROM alembic_version")
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1, f"Expected 1 alembic_version row, got {len(rows)}"
    actual = rows[0]
    assert actual == expected, (
        f"DB is at revision {actual!r} but the current head is {expected!r}. "
        "Run `alembic upgrade head` to apply pending migrations."
    )
