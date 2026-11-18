import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import db

pytestmark = pytest.mark.db_test


@pytest.fixture
def engine(db_session: AsyncSession) -> AsyncEngine:
    assert isinstance(db_session.bind, AsyncEngine)
    return db_session.bind


async def test_overlapping_transactions_orphan_nothing(engine: AsyncEngine):
    async with db.begin(), db.begin():
        assert db.checked_out_connections(engine.sync_engine) >= 2
        assert db.orphaned_connections() == 0


async def test_a_connection_held_outside_a_transaction_block_is_orphaned_until_returned(engine: AsyncEngine):
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))

        assert db.orphaned_connections() == 1

    assert db.orphaned_connections() == 0
