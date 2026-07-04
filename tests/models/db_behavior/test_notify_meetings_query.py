"""DB-integration tests for the starting-soon notification query (#201).

`USERS_TO_NOTIFY_STATEMENT` used to join users/settings through `meetups.owner_id`, so the
participant filters (status, notification toggle, lead-time window) were evaluated against
the meeting owner. Whether each participant is selected on their OWN settings is decided by
real SQL over real rows — a mock session can't evaluate the join/WHERE predicates, so these
cases live here. Throwaway data uses the 998_7xx range (single-session, never committed).
"""

import datetime as dt
from typing import cast

import pytest
from sqlmodel import col
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.cli.notify_meetings import USERS_TO_NOTIFY_STATEMENT
from mitup_bot.models import JoinedUsers, Meetup, Settings, User
from mitup_bot.models.users import UserStatus

pytestmark = pytest.mark.db_test


def make_member(tg_user_id: int, *, notification: bool = True, notification_time: int = 60) -> User:
    return User(
        first_name=f"Notify Query {tg_user_id}",
        tg_user_id=tg_user_id,
        status=UserStatus.MEMBER,
        settings=Settings(notification=notification, notification_time=notification_time),
    )


def make_meetup(owner: User, *, starts_in_minutes: int) -> Meetup:
    return Meetup(
        title="Notify Query Meeting",
        waiting_list=False,
        public=False,
        allow_invitation=False,
        incognito=False,
        owner=owner,
        datetime=dt.datetime.now(dt.UTC).replace(tzinfo=None) + dt.timedelta(minutes=starts_in_minutes),
    )


async def due_user_ids(db_session: AsyncSession, meetup: Meetup) -> set[int]:
    """Run the production statement scoped to one meeting, as `notify_joined_link` does."""
    statement = USERS_TO_NOTIFY_STATEMENT.where(col(JoinedUsers.meetup_id) == meetup.db_id)
    user_ids = {link.user_id for link in (await db_session.exec(statement)).all()}
    # The statement inner-joins users through joined_users.user_id, so NULL cannot survive it.
    assert None not in user_ids
    return cast(set[int], user_ids)


async def test_participants_are_filtered_on_their_own_notification_toggle(db_session: AsyncSession):
    """With the owner's notifications OFF, a participant with notifications ON is still
    selected, and a participant with notifications OFF is not — each joined link is
    evaluated against its own user's settings, not the owner's."""
    owner = make_member(998_700, notification=False)
    participant_on = make_member(998_701, notification=True)
    participant_off = make_member(998_702, notification=False)
    meetup = make_meetup(owner, starts_in_minutes=10)
    db_session.add_all([owner, participant_on, participant_off, meetup])
    await db_session.flush()
    db_session.add_all(
        [
            JoinedUsers(user=participant_on, meetup=meetup),
            JoinedUsers(user=participant_off, meetup=meetup),
        ]
    )
    await db_session.flush()

    assert await due_user_ids(db_session, meetup) == {participant_on.id}


async def test_owner_with_a_joined_link_is_notified_like_any_participant(db_session: AsyncSession):
    """An owner who joined their own meeting is selected on their own settings — there is
    no owner special-casing in either direction."""
    owner = make_member(998_710, notification=True)
    participant = make_member(998_711, notification=True)
    meetup = make_meetup(owner, starts_in_minutes=10)
    db_session.add_all([owner, participant, meetup])
    await db_session.flush()
    db_session.add_all(
        [
            JoinedUsers(user=owner, meetup=meetup),
            JoinedUsers(user=participant, meetup=meetup),
        ]
    )
    await db_session.flush()

    assert await due_user_ids(db_session, meetup) == {owner.id, participant.id}


async def test_lead_time_window_uses_each_participants_own_preference(db_session: AsyncSession):
    """For a meeting starting in 30 minutes, a participant with a 60-minute lead time is
    already inside their window while one with a 15-minute lead time is not yet."""
    owner = make_member(998_720, notification=False)
    participant_long_lead = make_member(998_721, notification_time=60)
    participant_short_lead = make_member(998_722, notification_time=15)
    meetup = make_meetup(owner, starts_in_minutes=30)
    db_session.add_all([owner, participant_long_lead, participant_short_lead, meetup])
    await db_session.flush()
    db_session.add_all(
        [
            JoinedUsers(user=participant_long_lead, meetup=meetup),
            JoinedUsers(user=participant_short_lead, meetup=meetup),
        ]
    )
    await db_session.flush()

    assert await due_user_ids(db_session, meetup) == {participant_long_lead.id}


async def test_waiting_list_and_left_participants_are_excluded(db_session: AsyncSession):
    """The participant-side filters still apply on top of the settings join: waiting-list
    links and users who left are not notified even with notifications ON."""
    owner = make_member(998_730, notification=True)
    waiting = make_member(998_731)
    left = make_member(998_732)
    left.status = UserStatus.LEFT
    member = make_member(998_733)
    meetup = make_meetup(owner, starts_in_minutes=10)
    db_session.add_all([owner, waiting, left, member, meetup])
    await db_session.flush()
    db_session.add_all(
        [
            JoinedUsers(user=waiting, meetup=meetup, is_waiting_list=True),
            JoinedUsers(user=left, meetup=meetup),
            JoinedUsers(user=member, meetup=meetup),
        ]
    )
    await db_session.flush()

    assert await due_user_ids(db_session, meetup) == {member.id}
