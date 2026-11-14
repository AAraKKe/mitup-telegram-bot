"""On real Postgres, every purge that deletes a meeting rewrites that meeting's tracked cards
into the deleted banner: the edits are rendered before the DELETE, reach Telegram only once it has
committed, and a card Telegram refuses leaves the purge standing.

Which meetings carry a card is decided by a correlated `EXISTS` over `messages`, the user purge
reaches its meetings through the `ON DELETE CASCADE` on `meetups.owner_id`, and the past-meetings
purge renders from a user-rooted load; a mock session can exercise none of them, so the cases live
here.

Committed cross-session data uses the 996_3xx tg range; every 997 sub-range is claimed elsewhere.
"""

import contextlib
import datetime as dt
from collections.abc import AsyncGenerator, Collection
from typing import Any, cast

import pytest
from sqlmodel import col, delete, select
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import User as TgUser
from telegram.error import Forbidden
from telegram.ext import Application, ExtBot

from mitup_bot import db
from mitup_bot.api_wrapper import EDIT_MESSAGE_TEXT_ENDPOINT, BotAdapter, TelegramApi
from mitup_bot.custom_context import MitupContext
from mitup_bot.events import delete_meetings, user_cleanup
from mitup_bot.handlers.main_menu.show_past_meetings import callback_query_confirm_delete_all_past_meetings
from mitup_bot.lifecycle import LifecyclePolicy
from mitup_bot.models import JoinedUsers, Meetup, Message, Settings, User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring.backend import NullBackend
from mitup_bot.monitoring.client import MetricsClient
from mitup_bot.translations import TranslationEngine
from mitup_bot.utils import MeetingDisplayMessages
from mitup_bot.utils import callbacks as cb
from tests.helpers import UpdateRequest
from tests.helpers.constants import FRESH_MESSAGE_DATE
from tests.helpers.context import build_context
from tests.helpers.fixtures import create_update
from tests.helpers.stub_bot import StubBot

pytestmark = pytest.mark.db_test

LANG = TranslationEngine.FALLBACK_LANG
RETENTION_DAYS = LifecyclePolicy.interval_days(LifecyclePolicy.get().inactive_retention)
DELETED_BANNER_HTML = MeetingDisplayMessages.DELETED_BANNER.rich(lang=LANG).html

# A card the bot can no longer reach is the ordinary case for a purged meeting, so every scenario
# runs twice: once with Telegram taking the edits and once with it refusing all of them.
DELIVERY = pytest.mark.parametrize("refuse", [False, True], ids=["cards_edited", "cards_unreachable"])


def days_ago(days: int) -> dt.datetime:
    return dt.datetime.now(dt.UTC) - dt.timedelta(days=days)


class CardProbeBot:
    """Stands in for ExtBot: records every card edit, and which of the purged meetings a fresh
    transaction could still read while that edit ran."""

    def __init__(self, meeting_ids: Collection[int], *, refuse: bool):
        self.meeting_ids = list(meeting_ids)
        self.refuse = refuse
        self.edits: list[dict[str, Any]] = []
        self.meetings_seen_during_edits: set[int] = set()

    async def do_api_request(self, endpoint: str, api_kwargs: dict[str, Any] | None = None, **_kwargs: object):
        if endpoint == EDIT_MESSAGE_TEXT_ENDPOINT:
            self.edits.append(api_kwargs or {})
            async with db.begin() as contender:
                present = await contender.exec(select(Meetup.id).where(col(Meetup.id).in_(self.meeting_ids)))
                self.meetings_seen_during_edits.update(cast(int, meeting_id) for meeting_id in present.all())
        if self.refuse:
            raise Forbidden("Forbidden: bot was blocked by the user")
        return True if endpoint == EDIT_MESSAGE_TEXT_ENDPOINT else None

    @property
    def edited_cards(self) -> set[tuple[int | None, int | None, str | None]]:
        return {(edit.get("chat_id"), edit.get("message_id"), edit.get("inline_message_id")) for edit in self.edits}

    @property
    def every_edit_shows_the_deleted_banner(self) -> bool:
        return all(DELETED_BANNER_HTML in edit["rich_message"]["html"] for edit in self.edits)


def make_probe_api(probe: CardProbeBot) -> TelegramApi:
    api = TelegramApi()
    api.adapter = BotAdapter(cast(ExtBot, probe), MetricsClient(NullBackend()))
    return api


def make_owner(tg_user_id: int, *, status: UserStatus = UserStatus.MEMBER, left_days_ago: int | None = None) -> User:
    return User(
        first_name=f"Purged Cards {tg_user_id}",
        tg_user_id=tg_user_id,
        status=status,
        left_time=None if left_days_ago is None else days_ago(left_days_ago),
        # Opted out of the deletion notice, so the retention purge settles without sending one.
        settings=Settings(language=LANG, deletion_notice=False),
    )


def make_meeting(owner: User, *, active: bool, retention_due: bool = False) -> Meetup:
    expired = days_ago(RETENTION_DAYS + 5) if retention_due else None
    return Meetup(
        title="Purged Cards Meeting",
        waiting_list=False,
        public=True,
        allow_invitation=False,
        incognito=False,
        owner=owner,
        active=active,
        expiration_notification_sent=retention_due,
        expiration_time=expired,
        warned_time=expired,
    )


@contextlib.asynccontextmanager
async def committed(tg_base: int, *rows: object) -> AsyncGenerator[None]:
    """Commit *rows*, and on the way out delete every user in the scenario's tg range, which
    cascades to whatever meetings and cards the purge under test left behind."""
    async with db.begin() as session:
        session.add_all(rows)
    try:
        yield
    finally:
        async with db.begin() as session:
            await session.exec(delete(User).where(col(User.tg_user_id).between(tg_base, tg_base + 9)))


async def surviving_meeting_ids(meeting_ids: Collection[int]) -> set[int]:
    async with db.begin() as session:
        rows = await session.exec(select(Meetup.id).where(col(Meetup.id).in_(list(meeting_ids))))
        return {cast(int, meeting_id) for meeting_id in rows.all()}


@DELIVERY
async def test_retention_purge_rewrites_the_cards_once_the_meetings_are_gone(db_session: AsyncSession, refuse: bool):
    tg_base = 996_310 if refuse else 996_300
    owner = make_owner(tg_base)
    carded = make_meeting(owner, active=False, retention_due=True)
    uncarded = make_meeting(owner, active=False, retention_due=True)
    private_card = Message(message_id=1, chat_id=tg_base, meetup=carded)
    shared_card = Message(inline_message_id=f"inline-{tg_base}", meetup=carded)

    async with committed(tg_base, owner, carded, uncarded, private_card, shared_card):
        meeting_ids = [carded.db_id, uncarded.db_id]
        probe = CardProbeBot(meeting_ids, refuse=refuse)

        outcome = await delete_meetings.delete_owner_chunk(make_probe_api(probe), [owner.db_id])

        assert {meetup.db_id for meetup in outcome.settled} == set(meeting_ids)
        assert probe.edited_cards == {(tg_base, 1, None), (None, None, f"inline-{tg_base}")}
        assert probe.every_edit_shows_the_deleted_banner
        assert probe.meetings_seen_during_edits == set()
        assert await surviving_meeting_ids(meeting_ids) == set()


@DELIVERY
async def test_demotion_rewrites_the_cards_of_the_meetings_it_deletes(db_session: AsyncSession, refuse: bool):
    tg_base = 996_330 if refuse else 996_320
    demotable = make_owner(tg_base, status=UserStatus.LEFT, left_days_ago=user_cleanup.LEFT_STATUS_GRACE_DAYS + 5)
    host = make_owner(tg_base + 1)
    hosted = make_meeting(host, active=True)
    # The link to an active meeting is what makes the LEFT user demotable instead of purgeable.
    link = JoinedUsers(user=demotable, meetup=hosted)
    carded = make_meeting(demotable, active=False)
    uncarded = make_meeting(demotable, active=False)
    group_card = Message(message_id=2, chat_id=-tg_base, meetup=carded)

    async with committed(tg_base, demotable, host, hosted, link, carded, uncarded, group_card):
        meeting_ids = [carded.db_id, uncarded.db_id]
        probe = CardProbeBot(meeting_ids, refuse=refuse)

        assert await user_cleanup.demote_stale_left_users(make_probe_api(probe)) == 1

        assert probe.edited_cards == {(-tg_base, 2, None)}
        assert probe.every_edit_shows_the_deleted_banner
        assert probe.meetings_seen_during_edits == set()
        assert await surviving_meeting_ids([*meeting_ids, hosted.db_id]) == {hosted.db_id}


@DELIVERY
async def test_user_purge_rewrites_the_cards_of_the_meetings_it_cascades_into(db_session: AsyncSession, refuse: bool):
    tg_base = 996_350 if refuse else 996_340
    left_owner = make_owner(tg_base, status=UserStatus.LEFT)
    finished = make_meeting(left_owner, active=False)
    uncarded = make_meeting(left_owner, active=False)
    finished_card = Message(message_id=3, chat_id=-tg_base, meetup=finished)
    # A deletion request takes the owner's meetings down while they are still live in their groups.
    erased_owner = make_owner(tg_base + 1, status=UserStatus.DELETION_REQUESTED)
    live = make_meeting(erased_owner, active=True)
    live_card = Message(inline_message_id=f"inline-{tg_base}", meetup=live)

    async with committed(tg_base, left_owner, finished, uncarded, finished_card, erased_owner, live, live_card):
        meeting_ids = [finished.db_id, uncarded.db_id, live.db_id]
        probe = CardProbeBot(meeting_ids, refuse=refuse)

        await user_cleanup.run(make_probe_api(probe), MetricsClient(NullBackend()))

        assert probe.edited_cards == {(-tg_base, 3, None), (None, None, f"inline-{tg_base}")}
        assert probe.every_edit_shows_the_deleted_banner
        assert probe.meetings_seen_during_edits == set()
        assert await surviving_meeting_ids(meeting_ids) == set()


async def test_past_meetings_purge_rewrites_the_cards_of_the_meetings_it_deletes(
    db_session: AsyncSession, app: Application
):
    """The owner's tap loads their meetings from their own row, the load whose eager chains stop at
    the User -> Meetup -> JoinedUsers -> User cycle; the cards must still render from it."""
    tg_base = 996_360
    owner = make_owner(tg_base)
    participant = make_owner(tg_base + 1)
    past = make_meeting(owner, active=False)
    link = JoinedUsers(user=participant, meetup=past)
    uncarded = make_meeting(owner, active=False)
    running = make_meeting(owner, active=True)
    cards = [
        Message(message_id=4, chat_id=tg_base, meetup=past),
        Message(inline_message_id=f"inline-{tg_base}", meetup=past),
        Message(inline_message_id=f"inline-{tg_base}-running", meetup=running),
    ]

    async with committed(tg_base, owner, participant, past, link, uncarded, running, *cards):
        meeting_ids = [past.db_id, uncarded.db_id]
        probe = CardProbeBot(meeting_ids, refuse=False)
        tg_owner = TgUser(id=tg_base, first_name="Purged Cards Owner", is_bot=False)
        # A fresh message is deleted rather than edited over, so every edit the probe sees is a card.
        update = create_update(
            UpdateRequest(callback_query=cb.CONFIRM_DELETE_ALL_PAST_MEETINGS, message_date=FRESH_MESSAGE_DATE),
            tg_user=tg_owner,
        )
        context = cast(MitupContext[StubBot, TelegramApi], build_context(update, app))
        context.api = make_probe_api(probe)

        await callback_query_confirm_delete_all_past_meetings(update, context)

        assert probe.edited_cards == {(tg_base, 4, None), (None, None, f"inline-{tg_base}")}
        assert probe.every_edit_shows_the_deleted_banner
        assert probe.meetings_seen_during_edits == set()
        assert await surviving_meeting_ids([*meeting_ids, running.db_id]) == {running.db_id}
