import re
from collections.abc import Callable
from typing import cast

import pytest
from sqlalchemy import Engine
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram import User as TgUser
from telegram.ext import Application

from mitup_bot.handlers.meeting.join_leave import handle_non_existing_user_join
from mitup_bot.models import JoinedUsers, Meetup, Settings, User
from mitup_bot.models.users import UserStatus
from mitup_bot.utils import MeetingJoinMessages
from mitup_bot.utils import callbacks as cb
from tests.helpers import UpdateRequest
from tests.helpers.context import build_context
from tests.helpers.fixtures import create_update

pytestmark = pytest.mark.db_test

OWNER_TG_USER_ID = 998_610
JOINER_TG_USER_ID = 998_611


def _async_engine(db_session: AsyncSession) -> AsyncEngine:
    """Re-wrap the session's sync-facade engine (its dialect is already async-capable)."""
    return AsyncEngine(cast(Engine, db_session.get_bind()))


def _inline_update(meeting_id: int, joiner: TgUser) -> Update:
    """A join clicked on a shared (inline) meeting message — reads user.joined_links."""
    return create_update(UpdateRequest(callback_query=cb.JOIN.with_id(meeting_id), from_bot_chat=False), tg_user=joiner)


def _bot_chat_update(meeting_id: int, joiner: TgUser) -> Update:
    """A join clicked in the bot chat — Message.from_update additionally reads user.meetups."""
    return create_update(UpdateRequest(callback_query=cb.JOIN.with_id(meeting_id)), tg_user=joiner)


@pytest.mark.parametrize("update_builder", [_inline_update, _bot_chat_update], ids=["inline", "bot_chat"])
async def test_unregistered_join_registers_user_and_creates_membership(
    db_session: AsyncSession,
    app: Application,
    update_builder: Callable[[int, TgUser], Update],
):
    """An unregistered user's join runs end-to-end against the real schema.

    `register_default_user` flushes the new JOINED_ONLY row and the join path immediately reads
    its collections (`joined_meeting`, and `own_meeting` via `Message.from_update`), which
    `User` marks lazy="raise". This is the regression pin for the explicit
    `session.refresh(new_user, ["joined_links", "meetups"])` after the flush — remove it and
    this test fails with InvalidRequestError from the raise guard.

    Runs on a dedicated session on the same engine (nothing committed; rolled back on close).
    `db_session` is depended on only to guarantee the schema is migrated.
    """
    async with AsyncSession(_async_engine(db_session)) as session:
        owner = User(first_name="RDU Owner", tg_user_id=OWNER_TG_USER_ID, settings=Settings())
        meeting = Meetup(
            title="RDU Meeting",
            waiting_list=False,
            public=False,
            allow_invitation=False,
            incognito=False,
            owner=owner,
        )
        session.add(owner)
        session.add(meeting)
        await session.flush()

        joiner = TgUser(id=JOINER_TG_USER_ID, first_name="RDU Joiner", is_bot=False)
        update = update_builder(meeting.db_id, joiner)
        context = build_context(update, app)
        match = re.match(cb.JOIN.pattern, str(cb.JOIN.with_id(meeting.db_id)))
        assert match is not None
        context.matches = [match]

        await handle_non_existing_user_join(session, update, context)

        # The user row was created with the JOINED_ONLY status.
        new_user = (await session.exec(select(User).where(User.tg_user_id == JOINER_TG_USER_ID))).first()
        assert new_user is not None
        assert new_user.status is UserStatus.JOINED_ONLY

        # The refreshed collections are readable and reflect the join (no raise-guard trip).
        assert new_user.joined_meeting(meeting.db_id) is not None
        assert new_user.own_meeting(meeting.db_id) is None  # joiner does not own the meeting

        # Exactly one membership row was created for the pair.
        memberships = (
            await session.exec(
                select(JoinedUsers).where(JoinedUsers.user_id == new_user.db_id, JoinedUsers.meetup_id == meeting.db_id)
            )
        ).all()
        assert len(memberships) == 1

        context.api.assert_answer_callback_query_called(
            update, text=MeetingJoinMessages.JOIN_UNREGISTERED.get(), show_alert=True
        )
        context.api.assert_method_just_called("update_meeting_messages")
