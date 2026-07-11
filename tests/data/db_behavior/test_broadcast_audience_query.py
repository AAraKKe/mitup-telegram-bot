"""Direct coverage for the broadcast recipient-estimate query on real Postgres.

`count_members_by_language` is the grouped aggregate behind the preview's per-language recipient
counts. It takes a session, so it is exercised from a fresh committed ``db.begin()`` transaction:
that transaction sees only committed rows, never the session fixture's uncommitted seeds, so this
test's committed users are the entire visible population (see the db-integration reference). This
file claims the 997_62x tg range.
"""

import contextlib
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import db
from mitup_bot.handlers.broadcast.utils import count_members_by_language
from mitup_bot.models import Settings, User
from mitup_bot.models.users import UserStatus

pytestmark = pytest.mark.db_test

ANONYMOUS_INVITEE_TG_ID = -1


@contextlib.asynccontextmanager
async def committed_audience(tg_base: int) -> AsyncIterator[None]:
    """Commit a mixed population and tear it down: two es_ES members, one de_DE member, a LEFT
    member (gl_ES) and the anonymous-invitee sentinel (-1, JOINED_ONLY, pt_BR) — the last two
    are non-members and must never be counted."""
    async with db.begin() as session:
        rows = [
            (tg_base, "es_ES", UserStatus.MEMBER),
            (tg_base + 1, "es_ES", UserStatus.MEMBER),
            (tg_base + 2, "de_DE", UserStatus.MEMBER),
            (tg_base + 3, "gl_ES", UserStatus.LEFT),
            (ANONYMOUS_INVITEE_TG_ID, "pt_BR", UserStatus.JOINED_ONLY),
        ]
        for tg_user_id, language, status in rows:
            user = User(first_name=f"User {tg_user_id}", tg_user_id=tg_user_id, status=status)
            user.settings = Settings(language=language)
            session.add(user)
        await session.flush()
    try:
        yield
    finally:
        async with db.begin() as session:
            await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text(
                    "DELETE FROM settings WHERE user_id IN "
                    "(SELECT id FROM users WHERE tg_user_id BETWEEN :lo AND :hi OR tg_user_id = :anon)"
                ).bindparams(lo=tg_base, hi=tg_base + 9, anon=ANONYMOUS_INVITEE_TG_ID)
            )
            await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text("DELETE FROM users WHERE tg_user_id BETWEEN :lo AND :hi OR tg_user_id = :anon").bindparams(
                    lo=tg_base, hi=tg_base + 9, anon=ANONYMOUS_INVITEE_TG_ID
                )
            )


async def test_count_members_by_language_groups_only_reachable_members(db_session: AsyncSession):
    tg_base = 997_620
    async with committed_audience(tg_base):
        async with db.begin() as session:
            counts = await count_members_by_language(session)

        # Only MEMBERs are grouped; the LEFT user (gl_ES) and the JOINED_ONLY -1 sentinel (pt_BR)
        # are excluded, so neither language key appears.
        assert counts == {"es_ES": 2, "de_DE": 1}
