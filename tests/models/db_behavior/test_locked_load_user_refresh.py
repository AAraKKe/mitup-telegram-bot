import re
from typing import cast

import pytest
from sqlalchemy import Engine
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram import User as TgUser
from telegram.ext import Application

from mitup_bot import guards
from mitup_bot.handlers.meeting.join_leave import user_joins_meeting
from mitup_bot.models import JoinedUsers, Meetup, Settings, User
from mitup_bot.utils import MeetingJoinMessages
from mitup_bot.utils import callbacks as cb
from tests.helpers import UpdateRequest
from tests.helpers.context import build_context
from tests.helpers.fixtures import create_update
from tests.helpers.types import StubMitupContext

pytestmark = pytest.mark.db_test

OWNER_TG_USER_ID = 998_620
PARTICIPANT_TG_USER_ID = 998_621


def async_engine(db_session: AsyncSession) -> AsyncEngine:
    """Re-wrap the session's sync-facade engine (its dialect is already async-capable)."""
    return AsyncEngine(cast(Engine, db_session.get_bind()))


async def seed_owned_meeting(session: AsyncSession) -> Meetup:
    owner = User(first_name="LLU Owner", tg_user_id=OWNER_TG_USER_ID, settings=Settings())
    meeting = Meetup(
        title="LLU Meeting",
        waiting_list=False,
        public=False,
        allow_invitation=False,
        incognito=False,
        owner=owner,
    )
    session.add(owner)
    session.add(meeting)
    await session.flush()
    return meeting


def join_context(update: Update, app: Application, meeting_id: int) -> StubMitupContext:
    context = build_context(update, app)
    match = re.match(cb.JOIN.pattern, str(cb.JOIN.with_id(meeting_id)))
    assert match is not None
    context.matches = [match]
    return context


async def test_owner_join_survives_locked_meeting_load(db_session: AsyncSession, app: Application):
    """An owner tapping Join on their own meeting must not trip the lazy="raise" guard.

    `handle_join_leave_operation` locks the meeting via `Meetup.by_id(..., for_update=True)`,
    which runs with populate_existing. That re-hydrates every entity the selectin cascade
    touches — including the meeting's owner, who here is the already-loaded current user —
    resetting `User.meetups`/`joined_links` (not part of the meeting's load) to unloaded. The
    next `own_meeting` access (`views.meeting.keyboard_for_update`) then raised InvalidRequestError
    (issue #207). This pins the post-lock `session.refresh` in `handle_join_leave_operation`.

    Runs on a dedicated session on the same engine (nothing committed; rolled back on close).
    `db_session` is depended on only to guarantee the schema is migrated.
    """
    async with AsyncSession(async_engine(db_session)) as session:
        meeting = await seed_owned_meeting(session)
        owner = await User.by_tg_user_id(session, OWNER_TG_USER_ID, must_exist=True)

        tg_owner = TgUser(id=OWNER_TG_USER_ID, first_name="LLU Owner", is_bot=False)
        # A bot-chat update: keyboard_for_update reads user.meetups via own_meeting.
        update = create_update(UpdateRequest(callback_query=cb.JOIN.with_id(meeting.db_id)), tg_user=tg_owner)
        context = join_context(update, app, meeting.db_id)

        await user_joins_meeting(session, update, context, owner)

        assert owner.own_meeting(meeting.db_id) is not None
        assert owner.joined_meeting(meeting.db_id) is not None
        context.api.assert_answer_callback_query_called(
            update, text=MeetingJoinMessages.JOIN_SUCCESS.get(lang=owner.lang), show_alert=False
        )
        context.api.assert_method_just_called("update_meeting_messages")


async def test_participant_rejoin_survives_locked_meeting_load(db_session: AsyncSession, app: Application):
    """Same populate_existing collateral, through the `JoinedUsers.user` selectin cascade.

    A mere participant's User is re-hydrated via the locked meeting's joined_links load, so the
    join operation's `joined_meeting` read (user.joined_links) tripped the lazy="raise" guard
    as well. The inline update keeps this variant on the joined_links read alone —
    keyboard_for_update only reads own_meeting for bot-chat messages.
    """
    async with AsyncSession(async_engine(db_session)) as session:
        meeting = await seed_owned_meeting(session)
        participant = User(first_name="LLU Participant", tg_user_id=PARTICIPANT_TG_USER_ID, settings=Settings())
        session.add(JoinedUsers(user=participant, meetup=meeting))
        await session.flush()

        acting_user = await User.by_tg_user_id(session, PARTICIPANT_TG_USER_ID, must_exist=True)
        tg_participant = TgUser(id=PARTICIPANT_TG_USER_ID, first_name="LLU Participant", is_bot=False)
        update = create_update(
            UpdateRequest(callback_query=cb.JOIN.with_id(meeting.db_id), from_bot_chat=False),
            tg_user=tg_participant,
        )
        context = join_context(update, app, meeting.db_id)

        await user_joins_meeting(session, update, context, acting_user)

        assert acting_user.joined_meeting(meeting.db_id) is not None
        assert acting_user.own_meeting(meeting.db_id) is None
        context.api.assert_answer_callback_query_called(
            update, text=MeetingJoinMessages.JOIN_ALREADY_JOINED.get(lang=acting_user.lang), show_alert=False
        )


async def test_meeting_accessible_for_update_returns_meeting_for_owner(db_session: AsyncSession, app: Application):
    """`meeting_accessible(..., for_update=True)` must survive its own populate_existing collateral.

    The guard checks ownership via `user.own_meeting` right after the locked load, so without
    the post-lock refresh inside the guard every for_update caller (kickout, edit participants,
    invite confirm) crashed for the meeting owner exactly like the join path in issue #207.
    """
    async with AsyncSession(async_engine(db_session)) as session:
        meeting = await seed_owned_meeting(session)
        owner = await User.by_tg_user_id(session, OWNER_TG_USER_ID, must_exist=True)

        tg_owner = TgUser(id=OWNER_TG_USER_ID, first_name="LLU Owner", is_bot=False)
        update = create_update(
            UpdateRequest(callback_query=cb.EDIT_MEETING_NO_LIMIT_PARTICIPANTS.with_id(meeting.db_id)),
            tg_user=tg_owner,
        )
        context = build_context(update, app)

        guarded_meeting = await guards.meeting_accessible(
            session, owner, meeting.db_id, "Edit no limit participants", update, context, for_update=True
        )

        assert guarded_meeting is not None
        assert guarded_meeting.db_id == meeting.db_id
        # The guard leaves the collections loaded for the handler body that follows it.
        assert owner.own_meeting(meeting.db_id) is not None
        assert owner.joined_meeting(meeting.db_id) is None
