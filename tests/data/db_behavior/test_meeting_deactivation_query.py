"""DB-integration tests for the dateless-meeting deactivation windows in `inactive_meetings`.

`MEETINGS_TO_DEACTIVATE_STATEMENT` deactivates a dateless meeting a year after creation, but only a
month after creation when its owner has LEFT the bot. The window is an interval comparison against
`created_time` gated on the owner's status — real SQL over real rows, which a mock session cannot
evaluate — so the cases live here. Throwaway data uses the 998_8xx range (single-session, never
committed).
"""

import datetime as dt

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.events.inactive_meetings import MEETINGS_TO_DEACTIVATE_STATEMENT
from mitup_bot.models import Meetup, Settings, User
from mitup_bot.models.users import UserStatus

pytestmark = pytest.mark.db_test


def make_owner(tg_user_id: int, *, status: UserStatus) -> User:
    return User(first_name=f"Deactivation {tg_user_id}", tg_user_id=tg_user_id, status=status, settings=Settings())


def make_dateless_meetup(owner: User, *, created_days_ago: int) -> Meetup:
    created = dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(days=created_days_ago)
    return Meetup(
        title="Dateless Deactivation Meeting",
        waiting_list=False,
        public=False,
        allow_invitation=False,
        incognito=False,
        owner=owner,
        datetime=None,
        created_time=created,
    )


async def due_meeting_ids(db_session: AsyncSession) -> set[int | None]:
    return {meeting.id for meeting in (await db_session.exec(MEETINGS_TO_DEACTIVATE_STATEMENT)).all()}


async def test_left_owner_dateless_meeting_older_than_a_month_is_due(db_session: AsyncSession):
    """A dateless meeting whose owner has LEFT deactivates once it is older than a month."""
    owner = make_owner(998_801, status=UserStatus.LEFT)
    db_session.add(owner)
    await db_session.flush()
    meeting = make_dateless_meetup(owner, created_days_ago=40)
    db_session.add(meeting)
    await db_session.flush()

    assert meeting.id in await due_meeting_ids(db_session)


async def test_left_owner_dateless_meeting_within_a_month_is_not_due(db_session: AsyncSession):
    """The LEFT-owner window is a month, not immediate: a two-week-old meeting is still active."""
    owner = make_owner(998_802, status=UserStatus.LEFT)
    db_session.add(owner)
    await db_session.flush()
    meeting = make_dateless_meetup(owner, created_days_ago=14)
    db_session.add(meeting)
    await db_session.flush()

    assert meeting.id not in await due_meeting_ids(db_session)


async def test_member_owner_dateless_meeting_older_than_a_month_is_not_due(db_session: AsyncSession):
    """The short window is LEFT-owner-only: a MEMBER owner's dateless meeting keeps the full year, so
    one older than a month but younger than a year is not yet due."""
    owner = make_owner(998_803, status=UserStatus.MEMBER)
    db_session.add(owner)
    await db_session.flush()
    meeting = make_dateless_meetup(owner, created_days_ago=40)
    db_session.add(meeting)
    await db_session.flush()

    assert meeting.id not in await due_meeting_ids(db_session)


async def test_member_owner_dateless_meeting_older_than_a_year_is_due(db_session: AsyncSession):
    """The general one-year rule still catches everyone: a MEMBER owner's dateless meeting older than
    a year is due."""
    owner = make_owner(998_804, status=UserStatus.MEMBER)
    db_session.add(owner)
    await db_session.flush()
    meeting = make_dateless_meetup(owner, created_days_ago=400)
    db_session.add(meeting)
    await db_session.flush()

    assert meeting.id in await due_meeting_ids(db_session)
