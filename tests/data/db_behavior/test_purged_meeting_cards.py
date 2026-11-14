"""On real Postgres, every purge that deletes a meeting rewrites that meeting's tracked cards
into the deleted banner: the edits are rendered before the DELETE, reach Telegram only once it has
committed, and a card Telegram refuses leaves the purge standing.

Which meetings carry a card is decided by a correlated `EXISTS` over `messages`, the user purge
reaches its meetings through the `ON DELETE CASCADE` on `meetups.owner_id`, and the past-meetings
purge renders from a user-rooted load; a mock session can exercise none of them, so the cases live
here.

Committed cross-session data uses the 996_3xx and 996_4xx tg ranges; every 997 sub-range is claimed
elsewhere.
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
from mitup_bot.utils import MeetingDisplayMessages, MeetingJoinMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views.meeting_text import title_content
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
    """Stands in for ExtBot: records every card edit and every other request, and which of the purged
    meetings a fresh transaction could still read while each edit ran."""

    def __init__(self, meeting_ids: Collection[int], *, refuse: bool):
        self.meeting_ids = list(meeting_ids)
        self.refuse = refuse
        self.edits: list[dict[str, Any]] = []
        self.other_requests: list[dict[str, Any]] = []
        self.meetings_seen_during_edits: set[int] = set()

    async def do_api_request(self, endpoint: str, api_kwargs: dict[str, Any] | None = None, **_kwargs: object):
        if endpoint != EDIT_MESSAGE_TEXT_ENDPOINT:
            self.other_requests.append(api_kwargs or {})
        else:
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


@DELIVERY
async def test_user_purge_takes_the_user_out_of_a_full_meeting_they_joined(db_session: AsyncSession, refuse: bool):
    """Erasing an account frees the place it held in somebody else's meeting the way leaving does:
    the next one waiting moves up and is told, and the meeting's card is redrawn without the name."""
    tg_base = 996_380 if refuse else 996_370
    leaving = make_owner(tg_base, status=UserStatus.DELETION_REQUESTED)
    owner = make_owner(tg_base + 1)
    waiter = make_owner(tg_base + 2)
    meeting = make_meeting(owner, active=True)
    meeting.max_members = 1
    meeting.waiting_list = True
    held_place = JoinedUsers(user=leaving, meetup=meeting, created_time=days_ago(2))
    waiting = JoinedUsers(user=waiter, meetup=meeting, is_waiting_list=True, created_time=days_ago(1))
    card = Message(inline_message_id=f"inline-{tg_base}", meetup=meeting)

    async with committed(tg_base, leaving, owner, waiter, meeting, held_place, waiting, card):
        probe = CardProbeBot([], refuse=refuse)

        await user_cleanup.run(make_probe_api(probe), MetricsClient(NullBackend()))

        async with db.begin() as session:
            links = (await session.exec(select(JoinedUsers).where(JoinedUsers.meetup_id == meeting.db_id))).all()
            purged = (await session.exec(select(User).where(User.tg_user_id == tg_base))).first()
        assert purged is None
        assert [(link.user_id, link.is_waiting_list) for link in links] == [(waiter.db_id, False)]
        assert tg_base + 2 in {request.get("chat_id") for request in probe.other_requests}
        assert probe.edited_cards == {(None, None, f"inline-{tg_base}")}
        [refreshed] = probe.edits
        assert waiter.first_name in refreshed["rich_message"]["html"]
        assert leaving.first_name not in refreshed["rich_message"]["html"]


# ---------------------------------------------------------------------------
# Who the deletion purge takes out of a joined meeting, and who it leaves alone
# ---------------------------------------------------------------------------


def full_meeting(owner: User) -> Meetup:
    """An active meeting with one place and a waiting list."""
    meeting = make_meeting(owner, active=True)
    meeting.max_members = 1
    meeting.waiting_list = True
    return meeting


def join(user: User, meeting: Meetup, *, days_before: int, waiting: bool = False) -> JoinedUsers:
    """A link the earlier the larger *days_before*, which is the order the waiting list moves in."""
    return JoinedUsers(user=user, meetup=meeting, is_waiting_list=waiting, created_time=days_ago(days_before))


async def committed_links(meeting: Meetup) -> list[tuple[int | None, bool]]:
    """Each committed link of *meeting* as its user id and whether it is waiting, oldest first."""
    async with db.begin() as session:
        statement = (
            select(JoinedUsers).where(JoinedUsers.meetup_id == meeting.db_id).order_by(col(JoinedUsers.created_time))
        )
        return [(link.user_id, link.is_waiting_list) for link in (await session.exec(statement)).all()]


async def surviving_user_ids(users: Collection[User]) -> set[int]:
    async with db.begin() as session:
        rows = await session.exec(select(User.id).where(col(User.id).in_([user.db_id for user in users])))
        return {cast(int, user_id) for user_id in rows.all()}


def promotions_to(probe: CardProbeBot, user: User, meeting: Meetup) -> int:
    promotion = MeetingJoinMessages.PROMOTED_FROM_WAITING_LIST.rich(lang=LANG, meeting_title=title_content(meeting))
    return sum(
        request.get("chat_id") == user.tg_user_id and promotion.html in request["rich_message"]["html"]
        for request in probe.other_requests
    )


async def test_two_leavers_in_one_meeting_promote_only_the_next_bystander(db_session: AsyncSession):
    """A confirmed and a waiting participant both erased: neither is told they got a place, and the
    one waiting behind them moves up once."""
    tg_base = 996_400
    confirmed = make_owner(tg_base, status=UserStatus.DELETION_REQUESTED)
    waiting = make_owner(tg_base + 1, status=UserStatus.DELETION_REQUESTED)
    bystander = make_owner(tg_base + 2)
    owner = make_owner(tg_base + 3)
    meeting = full_meeting(owner)
    links = [
        join(confirmed, meeting, days_before=3),
        join(waiting, meeting, days_before=2, waiting=True),
        join(bystander, meeting, days_before=1, waiting=True),
    ]

    async with committed(tg_base, confirmed, waiting, bystander, owner, meeting, *links):
        probe = CardProbeBot([], refuse=False)

        await user_cleanup.run(make_probe_api(probe), MetricsClient(NullBackend()))

        assert await committed_links(meeting) == [(bystander.db_id, False)]
        assert [promotions_to(probe, user, meeting) for user in (confirmed, waiting, bystander)] == [0, 0, 1]


async def test_a_meeting_that_went_inactive_keeps_its_links(db_session: AsyncSession):
    """Nominated while active, finished before its lock was taken: nobody leaves it and nobody moves up."""
    tg_base = 996_410
    leaving = make_owner(tg_base, status=UserStatus.DELETION_REQUESTED)
    bystander = make_owner(tg_base + 1)
    owner = make_owner(tg_base + 2)
    meeting = full_meeting(owner)
    meeting.active = False
    links = [join(leaving, meeting, days_before=2), join(bystander, meeting, days_before=1, waiting=True)]

    async with committed(tg_base, leaving, bystander, owner, meeting, *links):
        probe = CardProbeBot([], refuse=False)
        api = make_probe_api(probe)

        async with db.begin_write(api) as session:
            taken_out = await user_cleanup.remove_deletion_requested_participants(
                session, api, meeting.db_id, {leaving.db_id}
            )

        assert taken_out == 0
        assert await committed_links(meeting) == [(leaving.db_id, False), (bystander.db_id, True)]
        assert probe.edits == probe.other_requests == []


async def test_the_nomination_leaves_out_owned_and_inactive_meetings(db_session: AsyncSession):
    tg_base = 996_420
    leaving = make_owner(tg_base, status=UserStatus.DELETION_REQUESTED)
    host = make_owner(tg_base + 1)
    owned = make_meeting(leaving, active=True)
    finished = make_meeting(host, active=False)
    running = make_meeting(host, active=True)
    links = [join(leaving, meeting, days_before=1) for meeting in (owned, finished, running)]

    async with committed(tg_base, leaving, host, owned, finished, running, *links):
        async with db.begin() as session:
            statement = user_cleanup.MEETINGS_JOINED_BY_DELETION_REQUESTED_USERS_STATEMENT.where(
                col(User.id).in_([leaving.db_id])
            )
            nominated = set((await session.exec(statement)).all())

        assert nominated == {running.db_id}


async def test_a_meeting_whose_owner_is_also_erased_is_left_to_the_purge(db_session: AsyncSession):
    """The purge deletes that meeting, so nobody is promoted into it and its card only gets the banner."""
    tg_base = 996_430
    owner = make_owner(tg_base, status=UserStatus.DELETION_REQUESTED)
    leaving = make_owner(tg_base + 1, status=UserStatus.DELETION_REQUESTED)
    bystander = make_owner(tg_base + 2)
    meeting = full_meeting(owner)
    links = [join(leaving, meeting, days_before=2), join(bystander, meeting, days_before=1, waiting=True)]
    card = Message(inline_message_id=f"inline-{tg_base}", meetup=meeting)

    async with committed(tg_base, owner, leaving, bystander, meeting, *links, card):
        probe = CardProbeBot([meeting.db_id], refuse=False)

        await user_cleanup.run(make_probe_api(probe), MetricsClient(NullBackend()))

        assert promotions_to(probe, bystander, meeting) == 0
        assert probe.edited_cards == {(None, None, f"inline-{tg_base}")}
        assert probe.every_edit_shows_the_deleted_banner
        assert await surviving_meeting_ids([meeting.db_id]) == set()


async def test_a_leaver_who_was_only_waiting_frees_no_place(db_session: AsyncSession):
    tg_base = 996_440
    holder = make_owner(tg_base)
    leaving = make_owner(tg_base + 1, status=UserStatus.DELETION_REQUESTED)
    bystander = make_owner(tg_base + 2)
    owner = make_owner(tg_base + 3)
    meeting = full_meeting(owner)
    links = [
        join(holder, meeting, days_before=3),
        join(leaving, meeting, days_before=2, waiting=True),
        join(bystander, meeting, days_before=1, waiting=True),
    ]

    async with committed(tg_base, holder, leaving, bystander, owner, meeting, *links):
        probe = CardProbeBot([], refuse=False)

        await user_cleanup.run(make_probe_api(probe), MetricsClient(NullBackend()))

        assert await committed_links(meeting) == [(holder.db_id, False), (bystander.db_id, True)]
        assert promotions_to(probe, bystander, meeting) == 0


async def test_only_the_erased_account_leaves_a_meeting_it_shares_with_others(db_session: AsyncSession):
    """A LEFT, a MEMBER and a JOINED_ONLY participant of the same meeting keep their places and accounts."""
    tg_base = 996_450
    leaving = make_owner(tg_base, status=UserStatus.DELETION_REQUESTED)
    gone_quiet = make_owner(tg_base + 1, status=UserStatus.LEFT)
    member = make_owner(tg_base + 2)
    joined_only = make_owner(tg_base + 3, status=UserStatus.JOINED_ONLY)
    owner = make_owner(tg_base + 4)
    meeting = make_meeting(owner, active=True)
    bystanders = [gone_quiet, member, joined_only]
    links = [join(leaving, meeting, days_before=4)] + [
        join(user, meeting, days_before=3 - index) for index, user in enumerate(bystanders)
    ]

    async with committed(tg_base, leaving, *bystanders, owner, meeting, *links):
        await user_cleanup.run(make_probe_api(CardProbeBot([], refuse=False)), MetricsClient(NullBackend()))

        assert await committed_links(meeting) == [(user.db_id, False) for user in bystanders]
        assert await surviving_user_ids(bystanders) == {user.db_id for user in bystanders}


async def test_the_purge_spares_a_selected_user_who_no_longer_qualifies(db_session: AsyncSession):
    """The DELETE re-checks what the selects decided: a LEFT user who came back or joined an active
    meeting, and a deletion request no longer standing, all keep their rows."""
    tg_base = 996_460
    still_left = make_owner(tg_base, status=UserStatus.LEFT)
    came_back = make_owner(tg_base + 1)
    joined_since = make_owner(tg_base + 2, status=UserStatus.LEFT)
    request_withdrawn = make_owner(tg_base + 3)
    host = make_owner(tg_base + 4)
    meeting = make_meeting(host, active=True)
    link = join(joined_since, meeting, days_before=1)

    async with committed(tg_base, still_left, came_back, joined_since, request_withdrawn, host, meeting, link):
        async with db.begin() as session:
            purged = await user_cleanup.delete_users_still_purgeable(
                session, {still_left.db_id, came_back.db_id, joined_since.db_id}, {request_withdrawn.db_id}
            )

        assert purged == {still_left.db_id}
        spared = [came_back, joined_since, request_withdrawn]
        assert await surviving_user_ids([still_left, *spared]) == {user.db_id for user in spared}


class LateDeletionRequestProbe(CardProbeBot):
    """Marks another user DELETION_REQUESTED on the first request the run makes, which the leave step
    sends after the run has taken its snapshot of who to erase."""

    def __init__(self, late_tg_user_id: int):
        super().__init__([], refuse=False)
        self.late_tg_user_id = late_tg_user_id
        self.marked = False

    async def do_api_request(self, endpoint: str, api_kwargs: dict[str, Any] | None = None, **kwargs: object):
        if not self.marked:
            self.marked = True
            async with db.begin() as session:
                late = (await session.exec(select(User).where(User.tg_user_id == self.late_tg_user_id))).one()
                late.set_status(UserStatus.DELETION_REQUESTED)
        return await super().do_api_request(endpoint, api_kwargs, **kwargs)


async def test_a_deletion_requested_after_the_snapshot_waits_for_the_next_run(db_session: AsyncSession):
    """Only the users in the run's snapshot are erased: one who asks while the run is underway has not
    left their meetings yet, so their row stays until the next run."""
    tg_base = 996_470
    leaving = make_owner(tg_base, status=UserStatus.DELETION_REQUESTED)
    late = make_owner(tg_base + 1)
    owner = make_owner(tg_base + 2)
    meeting = make_meeting(owner, active=True)
    link = join(leaving, meeting, days_before=1)
    card = Message(inline_message_id=f"inline-{tg_base}", meetup=meeting)

    async with committed(tg_base, leaving, late, owner, meeting, link, card):
        probe = LateDeletionRequestProbe(late.tg_user_id)

        await user_cleanup.run(make_probe_api(probe), MetricsClient(NullBackend()))

        assert probe.marked
        assert await surviving_user_ids([leaving, late]) == {late.db_id}


async def test_a_meeting_the_leave_step_failed_on_still_loses_the_erased_link(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
):
    """The erasure is not held back: the cascade removes the link without promoting anyone, and the
    run reports the failure."""
    tg_base = 996_480
    leaving = make_owner(tg_base, status=UserStatus.DELETION_REQUESTED)
    bystander = make_owner(tg_base + 1)
    owner = make_owner(tg_base + 2)
    meeting = full_meeting(owner)
    links = [join(leaving, meeting, days_before=2), join(bystander, meeting, days_before=1, waiting=True)]

    async def fail(*_args: object, **_kwargs: object) -> int:
        raise RuntimeError("leave step failed")

    monkeypatch.setattr(user_cleanup, "remove_deletion_requested_participants", fail)

    async with committed(tg_base, leaving, bystander, owner, meeting, *links):
        probe = CardProbeBot([], refuse=False)

        with pytest.raises(RuntimeError, match="1 meetings"):
            await user_cleanup.run(make_probe_api(probe), MetricsClient(NullBackend()))

        assert await surviving_user_ids([leaving]) == set()
        assert await committed_links(meeting) == [(bystander.db_id, True)]
        assert promotions_to(probe, bystander, meeting) == 0


async def test_the_demotion_spares_a_candidate_who_is_no_longer_left(db_session: AsyncSession):
    """A user nominated while LEFT who came back keeps their status and their finished meetings; the
    one still LEFT is demoted and loses theirs, cards included."""
    tg_base = 996_490
    grace_expired = user_cleanup.LEFT_STATUS_GRACE_DAYS + 5
    still_left = make_owner(tg_base, status=UserStatus.LEFT, left_days_ago=grace_expired)
    came_back = make_owner(tg_base + 1)
    host = make_owner(tg_base + 2)
    hosted = make_meeting(host, active=True)
    links = [join(user, hosted, days_before=1) for user in (still_left, came_back)]
    finished_left = make_meeting(still_left, active=False)
    finished_back = make_meeting(came_back, active=False)
    cards = [
        Message(message_id=5, chat_id=-tg_base, meetup=finished_left),
        Message(message_id=6, chat_id=-tg_base - 1, meetup=finished_back),
    ]

    async with committed(tg_base, still_left, came_back, host, hosted, *links, finished_left, finished_back, *cards):
        probe = CardProbeBot([], refuse=False)
        api = make_probe_api(probe)

        async with db.begin_write(api) as session:
            demoted = await user_cleanup.demote_users_still_demotable(session, api, {still_left.db_id, came_back.db_id})

        assert [user.db_id for user in demoted] == [still_left.db_id]
        async with db.begin() as session:
            statuses = dict(
                (
                    await session.exec(
                        select(User.id, User.status).where(col(User.id).in_([still_left.db_id, came_back.db_id]))
                    )
                ).all()
            )
        assert statuses == {still_left.db_id: UserStatus.JOINED_ONLY, came_back.db_id: UserStatus.MEMBER}
        assert await surviving_meeting_ids([finished_left.db_id, finished_back.db_id]) == {finished_back.db_id}
        assert probe.edited_cards == {(-tg_base, 5, None)}
