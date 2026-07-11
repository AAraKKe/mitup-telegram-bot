import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram.ext import ApplicationHandlerStop

from mitup_bot import db
from mitup_bot.handlers.registration_process.utils import claim_update
from mitup_bot.models import Settings, User

pytestmark = pytest.mark.db_test

CLAIM_PROBE_TG_USER_ID = 998_600  # unique across the db_behavior suite


async def test_claim_update_commits_before_the_stop_propagates(db_session: AsyncSession):
    """``claim_update`` wraps OUTSIDE ``with_session``, so the handler's transaction commits
    before ``ApplicationHandlerStop`` unwinds. If the decorators were ever nested the other way
    around, the raise would happen inside the session scope and roll the write back — this test
    would then fail because the row would not be visible from a fresh session.

    ``db_session`` is depended on only to guarantee the engine is configured and the schema is
    migrated; the probe runs through ``with_session`` on the module-level sessionmaker, exactly
    as the production registration handlers do.
    """

    @claim_update
    @db.with_session
    async def probe_handler(session: AsyncSession) -> int:
        session.add(User(first_name="Claim Probe", tg_user_id=CLAIM_PROBE_TG_USER_ID, settings=Settings()))
        return 42  # stands in for the conversation state the real handlers carry

    with pytest.raises(ApplicationHandlerStop) as stop:
        await probe_handler()
    assert stop.value.state == 42  # the raised stop carries the handler's return value as state

    # A fresh session sees the row only if the transaction committed before the stop unwound.
    async with db.begin() as fresh_session:
        row = (await fresh_session.exec(select(User).where(User.tg_user_id == CLAIM_PROBE_TG_USER_ID))).first()
        assert row is not None
        # Leave no committed state behind for the rest of the suite (Settings cascades from User).
        await fresh_session.delete(row)
