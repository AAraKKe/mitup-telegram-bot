import pytest
from sqlalchemy import text
from sqlmodel import Session

pytestmark = pytest.mark.db_test


@pytest.mark.parametrize(
    "table_name, column_name",
    [("users", "first_name"), ("meetups", "title")],
    ids=["users.first_name", "meetups.title"],
)
def test_column_is_not_nullable(db_session: Session, table_name: str, column_name: str) -> None:
    """Verify NOT NULL constraints using schema introspection (raw SQL bypasses Python defaults)."""
    is_nullable = db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/376
        text(
            "SELECT is_nullable FROM information_schema.columns WHERE table_name=:table AND column_name=:col"
        ).bindparams(table=table_name, col=column_name)
    ).scalar_one()
    assert is_nullable == "NO"
