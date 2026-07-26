import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.lifecycle import LifecyclePolicy
from mitup_bot.models import Settings, User

pytestmark = pytest.mark.db_test

SET_TIMEOUT_SQL = text("UPDATE settings SET timeout = :timeout WHERE user_id = :user_id")


def make_probe_user(timeout: int) -> User:
    """A throwaway user in the 998 range, carrying settings with the given timeout."""
    return User(first_name="Timeout Probe", tg_user_id=998_400, settings=Settings(timeout=timeout))


async def test_over_cap_timeout_is_rejected(db_session: AsyncSession):
    """The ck_settings_timeout_max constraint rejects an over-cap timeout even when the write
    bypasses the model — an unbounded timeout keeps its owner's dated meetings active forever."""
    savepoint = await db_session.begin_nested()
    try:
        user = make_probe_user(LifecyclePolicy.get().max_timeout_minutes)
        db_session.add(user)
        await db_session.flush()

        # The model refuses the assignment, so the constraint is only reachable through a
        # statement that skips it.
        with pytest.raises(IntegrityError):
            await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                SET_TIMEOUT_SQL.bindparams(timeout=LifecyclePolicy.get().max_timeout_minutes + 1, user_id=user.id)
            )
    finally:
        await savepoint.rollback()


async def test_timeout_at_cap_is_accepted(db_session: AsyncSession):
    savepoint = await db_session.begin_nested()
    try:
        user = make_probe_user(LifecyclePolicy.get().max_timeout_minutes)
        db_session.add(user)
        await db_session.flush()

        assert user.settings.timeout == LifecyclePolicy.get().max_timeout_minutes
    finally:
        await savepoint.rollback()
