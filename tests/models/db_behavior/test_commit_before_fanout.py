"""Empirical proof of #188's acceptance criterion: under ``with_session(write=True)`` the
per-meeting row lock from #187 is released at commit, BEFORE any queued Telegram call runs.

The probe bot's ``send_message`` executes during the outbox drain and, from a second
``db.begin()`` transaction, takes the same ``SELECT … FOR UPDATE`` the handler held. If the
decorator still held the handler's transaction across the fan-out, that locked read would
block until RACE_TIMEOUT and fail the test; committed-before-drain, it acquires the lock
immediately and observes the handler's committed membership row.
"""

import asyncio
from types import SimpleNamespace
from typing import cast

import pytest
from sqlmodel import select, text
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram.ext import ExtBot

from mitup_bot import db
from mitup_bot.api_wrapper import BotAdapter, TelegramApi
from mitup_bot.models import Meetup, Settings, User
from mitup_bot.models.joined_users import JOINED_USERS_UNIQUE_CONSTRAINT
from mitup_bot.monitoring.backend import NullBackend
from mitup_bot.monitoring.client import MetricsClient

pytestmark = pytest.mark.db_test

RACE_TIMEOUT = 20.0


class ProbeBot:
    """Stands in for ExtBot during the drain: contends for the meeting row lock."""

    def __init__(self, meetup_id: int):
        self.meetup_id = meetup_id
        self.order: list[str] = []
        self.participants_seen_under_lock: int | None = None

    async def send_message(self, **kwargs: object):
        self.order.append("fanout-started")
        # Take the same row lock the handler held. This blocks (and times the test out)
        # if the handler's transaction were still open around the fan-out.
        async with db.begin() as contender:
            locked = await Meetup.by_id(contender, self.meetup_id, for_update=True)
            assert locked is not None
            self.participants_seen_under_lock = locked.n_participants
        self.order.append("lock-acquired-during-fanout")


async def test_row_lock_released_at_commit_before_queued_fanout(db_session: AsyncSession):
    # Committed fixtures: the concurrent contender transaction cannot see the session
    # fixture's uncommitted seeds. Committed cross-session data uses the 997 range per
    # the db-integration reference; this file claims the 997_1xx sub-range.
    async with db.begin() as session:
        owner = User(first_name="Fanout Owner", tg_user_id=997_100, settings=Settings())
        joiner = User(first_name="Fanout Joiner", tg_user_id=997_101, settings=Settings())
        meetup = Meetup(
            title="Fanout Meeting",
            waiting_list=False,
            public=False,
            allow_invitation=False,
            incognito=False,
            owner=owner,
        )
        session.add_all([owner, joiner, meetup])
        await session.flush()
        meetup_id = cast(int, meetup.id)

    probe = ProbeBot(meetup_id)
    api = TelegramApi()
    api.adapter = BotAdapter(cast(ExtBot, probe), MetricsClient(NullBackend()))
    # The decorator locates the api on any argument exposing `.api` — a stand-in context is
    # enough, no PTB machinery required.
    context = SimpleNamespace(api=api)

    @db.with_session(write=True)
    async def join_handler(session: AsyncSession, context: SimpleNamespace):
        user = (await session.exec(select(User).where(User.tg_user_id == 997_101))).one()
        await session.refresh(user, ["joined_links"])
        meeting = await Meetup.by_id(session, meetup_id, for_update=True)
        assert meeting is not None
        link = await db.racy_flush(
            session, lambda: meeting.add_participant(user), constraint=JOINED_USERS_UNIQUE_CONSTRAINT
        )
        assert link is not None
        # The handler's linear style: the send is enqueued here, executed only after commit.
        await context.api.send_message_to_user(user, "you joined")

    try:
        async with asyncio.timeout(RACE_TIMEOUT):
            await join_handler(context)

        assert probe.order == ["fanout-started", "lock-acquired-during-fanout"]
        # The contender, serialized only by its own locked read, saw the handler's COMMITTED
        # membership — the transaction was fully over before the fan-out began.
        assert probe.participants_seen_under_lock == 1
    finally:
        async with db.begin() as session:
            await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text("DELETE FROM meetups WHERE id = :mid").bindparams(mid=meetup_id)
            )
            await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text("DELETE FROM users WHERE tg_user_id IN (997100, 997101)")
            )
