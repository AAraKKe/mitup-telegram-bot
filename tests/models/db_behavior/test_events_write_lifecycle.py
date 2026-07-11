"""Empirical proof of #199's acceptance criteria on real Postgres: the recurrent-event jobs
drive ``db.begin_write`` per meeting, so no transaction or row lock is held across Telegram
I/O, and their DB fix-ups (unreachable users) land via the lifecycle's reconcile.

Each probe bot executes during the outbox drain and opens a fresh ``db.begin()``
transaction: whatever it observes there is committed state. If a job still held its
transaction (or the meetup row lock) across the fan-out, the probes would see the stale
pre-commit state or block until RACE_TIMEOUT.

Committed cross-session data uses the 997 range per the db-integration reference; this file
claims the 997_4xx sub-range (997_0xx: test_meeting_row_locks, 997_1xx:
test_commit_before_fanout, 997_2xx: test_timestamps, 997_3xx:
test_inactive_meetings_row_locks).
"""

import asyncio
import contextlib
import datetime as dt
from collections.abc import AsyncIterator
from typing import cast

import pytest
from sqlalchemy import text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram.error import Forbidden
from telegram.ext import ExtBot

from mitup_bot import db
from mitup_bot.api_wrapper import BotAdapter, TelegramApi
from mitup_bot.events import inactive_meetings, notify_meetings_started, user_cleanup
from mitup_bot.models import Meetup, Message, Settings, User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring.backend import NullBackend
from mitup_bot.monitoring.client import MetricsClient

pytestmark = pytest.mark.db_test

RACE_TIMEOUT = 20.0


def make_probe_api(probe: object) -> TelegramApi:
    api = TelegramApi()
    api.adapter = BotAdapter(cast(ExtBot, probe), MetricsClient(NullBackend()))
    return api


@contextlib.asynccontextmanager
async def provisioned_started_meeting(tg_base: int, *, with_message: bool = False) -> AsyncIterator[int]:
    """Provision a committed meeting whose scheduled time has passed, owned by ``tg_base``
    with the MEMBER participant ``tg_base + 1``, and tear it down afterwards.

    Committed data is required: the per-meeting write lifecycles and the probes' contender
    transactions cannot see the session fixture's uncommitted seeds. The meeting's
    ``datetime`` is set far enough in the past that it is due for both the started
    notification and (any owner timeout having elapsed) the expiration sweep.
    """
    started = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(days=2)
    async with db.begin() as session:
        owner = User(first_name="Lifecycle Owner", tg_user_id=tg_base, settings=Settings())
        participant = User(first_name="Lifecycle Participant", tg_user_id=tg_base + 1, settings=Settings())
        meetup = Meetup(
            title="Lifecycle Meeting",
            waiting_list=False,
            public=False,
            allow_invitation=False,
            incognito=False,
            max_members=None,
            owner=owner,
            datetime=started,
        )
        link = meetup.create_joined_link(participant, is_waiting_list=False)
        session.add_all([owner, participant, meetup, link])
        if with_message:
            session.add(Message(message_id=555, chat_id=tg_base, meetup=meetup))
        await session.flush()
        meetup_id = meetup.db_id
    try:
        yield meetup_id
    finally:
        async with db.begin() as session:
            await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text("DELETE FROM messages WHERE meetup_id = :mid").bindparams(mid=meetup_id)
            )
            await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text("DELETE FROM meetups WHERE id = :mid").bindparams(mid=meetup_id)
            )
            await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text("DELETE FROM users WHERE tg_user_id BETWEEN :lo AND :hi").bindparams(lo=tg_base, hi=tg_base + 1)
            )


async def committed_user_status(tg_user_id: int) -> UserStatus:
    async with db.begin() as session:
        user = (await session.exec(select(User).where(User.tg_user_id == tg_user_id))).one()
        return user.status


class FlagProbeBot:
    """Stands in for ExtBot during the notify drain: reads the committed flag concurrently."""

    def __init__(self, meetup_id: int):
        self.meetup_id = meetup_id
        self.flag_seen_during_drain: bool | None = None

    async def send_message(self, **kwargs: object):
        # A fresh transaction only sees committed state: True here proves the job's
        # transaction was over before the queued send executed.
        async with db.begin() as contender:
            meeting = await Meetup.by_id(contender, self.meetup_id)
            assert meeting is not None
            self.flag_seen_during_drain = meeting.started_notification_sent


class BlockedBot:
    """Every DM raises Forbidden, as when the participant has blocked the bot."""

    async def send_message(self, **kwargs: object):
        raise Forbidden("Forbidden: bot was blocked by the user")


class LockProbeBot:
    """Stands in for ExtBot during the expiration drain: contends for the meetup row lock."""

    def __init__(self, meetup_id: int):
        self.meetup_id = meetup_id
        self.active_seen_under_lock: bool | None = None

    async def edit_message_text(self, **kwargs: object):
        # Takes the same FOR UPDATE lock the critical section held. This blocks (and times
        # the test out) if the job's transaction were still open around the fan-out.
        async with db.begin() as contender:
            locked = await Meetup.by_id(contender, self.meetup_id, for_update=True)
            assert locked is not None
            self.active_seen_under_lock = locked.active


async def test_notify_started_commits_flag_before_the_drain(db_session: AsyncSession):
    tg_base = 997_400
    async with provisioned_started_meeting(tg_base) as meetup_id:
        probe = FlagProbeBot(meetup_id)

        async with asyncio.timeout(RACE_TIMEOUT):
            notified = await notify_meetings_started.notify_meeting_started(meetup_id, make_probe_api(probe))

        assert notified == 1
        # The queued send observed the committed flag: a crash between commit and drain
        # loses at most the rendering, never re-notifies the meeting.
        assert probe.flag_seen_during_drain is True


async def test_notify_started_marks_blocked_participant_inactive_via_reconcile(db_session: AsyncSession):
    tg_base = 997_410
    async with provisioned_started_meeting(tg_base) as meetup_id:
        notified = await notify_meetings_started.notify_meeting_started(meetup_id, make_probe_api(BlockedBot()))

        assert notified == 1  # the send was enqueued; its failure surfaced only at drain time
        # The drain recorded the unreachable user and the reconcile transaction committed
        # the MEMBER → LEFT transition — no CLI code touched the DB after the fan-out.
        assert await committed_user_status(tg_base + 1) is UserStatus.LEFT

        async with db.begin() as session:
            meeting = await Meetup.by_id(session, meetup_id)
            assert meeting is not None
            assert meeting.started_notification_sent is True


async def test_deactivation_releases_row_lock_before_the_drain(db_session: AsyncSession):
    tg_base = 997_420
    async with provisioned_started_meeting(tg_base, with_message=True) as meetup_id:
        probe = LockProbeBot(meetup_id)

        async with asyncio.timeout(RACE_TIMEOUT):
            deactivated = await inactive_meetings.deactivate_meeting(meetup_id, make_probe_api(probe))

        assert deactivated is True
        # The contender, serialized only by its own locked read, acquired the lock during
        # the drain and saw the COMMITTED deactivation: begin_write released the row lock
        # at commit, before any Telegram call ran.
        assert probe.active_seen_under_lock is False


@contextlib.asynccontextmanager
async def provisioned_marked_user(tg_user_id: int) -> AsyncIterator[None]:
    """Provision a committed DELETION_REQUESTED user (with a Settings row) and tear down
    whatever the purge under test may have left behind."""
    async with db.begin() as session:
        session.add(
            User(first_name="Marked", tg_user_id=tg_user_id, status=UserStatus.DELETION_REQUESTED, settings=Settings())
        )
    try:
        yield
    finally:
        async with db.begin() as session:
            await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text("DELETE FROM users WHERE tg_user_id = :uid").bindparams(uid=tg_user_id)
            )


class ErasureProbeBot:
    """Stands in for ExtBot during the farewell drain: reads the committed user row concurrently."""

    def __init__(self, tg_user_id: int):
        self.tg_user_id = tg_user_id
        self.row_present_during_drain: bool | None = None

    async def send_message(self, **kwargs: object):
        # A fresh transaction only sees committed state: an absent row here proves the purge
        # committed before the farewell executed — the deletion is never announced early.
        async with db.begin() as contender:
            user = (await contender.exec(select(User).where(User.tg_user_id == self.tg_user_id))).first()
            self.row_present_during_drain = user is not None


async def test_user_cleanup_commits_the_purge_before_the_farewell(db_session: AsyncSession):
    tg_user_id = 997_430
    async with provisioned_marked_user(tg_user_id):
        probe = ErasureProbeBot(tg_user_id)

        async with asyncio.timeout(RACE_TIMEOUT):
            await user_cleanup.run(make_probe_api(probe), MetricsClient(NullBackend()))

        assert probe.row_present_during_drain is False


async def test_user_cleanup_deletion_stands_when_the_farewell_fails(db_session: AsyncSession):
    tg_user_id = 997_440
    async with provisioned_marked_user(tg_user_id):
        # The blocked farewell surfaces at drain time, is swallowed by the lifecycle (the
        # reconcile finds no row to mark inactive), and the run completes without raising.
        await user_cleanup.run(make_probe_api(BlockedBot()), MetricsClient(NullBackend()))

        async with db.begin() as session:
            purged = (await session.exec(select(User).where(User.tg_user_id == tg_user_id))).first()
            assert purged is None
