import json
from typing import cast

import pytest
from sqlalchemy import Engine
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.handlers.privacy import data_export
from mitup_bot.models import JoinedUsers, Meetup, Settings, SupporterSubscription, User

pytestmark = pytest.mark.db_test

EXPORTER_TG_USER_ID = 998_800
FRIEND_TG_USER_ID = 998_801
ORGANIZER_TG_USER_ID = 998_802


def async_engine(db_session: AsyncSession) -> AsyncEngine:
    """Re-wrap the session's sync-facade engine (its dialect is already async-capable)."""
    return AsyncEngine(cast(Engine, db_session.get_bind()))


async def seed_export_graph(session: AsyncSession):
    """Seed the full export surface: an owned meeting with a participant and a waiting-list
    entry, a join to someone else's meeting, and a Patreon link."""
    exporter = User(first_name="Export Exporter", tg_user_id=EXPORTER_TG_USER_ID, settings=Settings())
    friend = User(first_name="Export Friend", tg_user_id=FRIEND_TG_USER_ID, settings=Settings())
    organizer = User(first_name="Export Organizer", tg_user_id=ORGANIZER_TG_USER_ID, settings=Settings())
    own_meeting = Meetup(
        title="Export Own Meeting",
        waiting_list=True,
        public=False,
        allow_invitation=False,
        incognito=False,
        max_members=1,
        owner=exporter,
    )
    other_meeting = Meetup(
        title="Export Other Meeting",
        waiting_list=False,
        public=False,
        allow_invitation=False,
        incognito=False,
        owner=organizer,
    )
    session.add_all([exporter, friend, organizer, own_meeting, other_meeting])
    await session.flush()
    session.add(JoinedUsers(user=friend, meetup=own_meeting))
    session.add(JoinedUsers(user=exporter, meetup=other_meeting))
    session.add(SupporterSubscription(user_id=exporter.db_id, patreon_user_id="export-patreon-1"))
    await session.flush()


async def test_build_user_export_loads_its_whole_graph_on_real_postgres(db_session: AsyncSession):
    """The builder's explicit selectinload chains must cover every hop it traverses
    (meeting -> links -> user, link -> meetup -> owner): on the async engine any uncovered
    hop raises MissingGreenlet instead of lazily loading. A lean user proves the builder
    depends on no pre-loaded collections."""
    async with AsyncSession(async_engine(db_session)) as session:
        await seed_export_graph(session)
        # Force a clean re-load so the export exercises the builder's own loading, not the
        # collections already populated by the seeding flushes.
        session.expunge_all()

        exporter = await User.by_tg_user_id(session, EXPORTER_TG_USER_ID, must_exist=True, load_collections=False)
        export = await data_export.build_user_export(session, exporter)

        assert export["user"]["telegram_user_id"] == EXPORTER_TG_USER_ID
        assert [meeting["title"] for meeting in export["meetings"]] == ["Export Own Meeting"]
        assert export["meetings"][0]["participants"] == ["Export Friend"]
        assert [joined["meeting_title"] for joined in export["joined_meetings"]] == ["Export Other Meeting"]
        assert export["joined_meetings"][0]["organizer"] == "Export Organizer"
        assert export["patreon"]["patreon_user_id"] == "export-patreon-1"


async def test_export_on_real_postgres_never_leaks_other_users_telegram_ids(db_session: AsyncSession):
    async with AsyncSession(async_engine(db_session)) as session:
        await seed_export_graph(session)
        session.expunge_all()

        exporter = await User.by_tg_user_id(session, EXPORTER_TG_USER_ID, must_exist=True, load_collections=False)
        export = await data_export.build_user_export(session, exporter)

        serialized = json.dumps(export)
        assert str(FRIEND_TG_USER_ID) not in serialized
        assert str(ORGANIZER_TG_USER_ID) not in serialized
        assert str(EXPORTER_TG_USER_ID) in serialized
