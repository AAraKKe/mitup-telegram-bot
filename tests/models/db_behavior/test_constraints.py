import pytest
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

pytestmark = pytest.mark.db_test


@pytest.mark.parametrize(
    "table_name, column_name",
    [("users", "first_name"), ("meetups", "title")],
    ids=["users.first_name", "meetups.title"],
)
async def test_column_is_not_nullable(db_session: AsyncSession, table_name: str, column_name: str):
    """Verify NOT NULL constraints using schema introspection (raw SQL bypasses Python defaults)."""
    is_nullable = (
        await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text(
                "SELECT is_nullable FROM information_schema.columns WHERE table_name=:table AND column_name=:col"
            ).bindparams(table=table_name, col=column_name)
        )
    ).scalar_one()
    assert is_nullable == "NO"
