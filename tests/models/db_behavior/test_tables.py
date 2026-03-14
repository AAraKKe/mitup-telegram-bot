import pytest
from sqlalchemy import text
from sqlmodel import Session

pytestmark = pytest.mark.db_test


@pytest.mark.parametrize("table_name", ["users", "settings", "meetups", "joined_users", "messages"])
def test_table_exists(db_session: Session, table_name: str) -> None:
    result = db_session.exec(  # type: ignore[call-overload]
        text(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name=:name"
        ).bindparams(name=table_name)
    ).scalar_one()
    assert result == 1
