"""DB-integration tests for the LEFT-user retention guards in `user_cleanup`.

`INACTIVE_USERS_SELECT_STATEMENT` only nominates a LEFT user for deletion once no active meeting
depends on them — neither an active meeting they own nor an active meeting they joined. Those
guards are two `NOT EXISTS` subqueries whose truth is decided by real SQL over real rows; a mock
session cannot evaluate them, so the cases live here. `DELETION_REQUESTED_USERS_SELECT_STATEMENT`
carries no such guard — a privacy erasure proceeds regardless of meetings — which is pinned too.

`DEMOTABLE_LEFT_USERS_SELECT_STATEMENT` splits the retained users: past the grace period, one whose
only remaining tie is a join link is demoted instead of kept whole. Its grace comparison is
`left_time + interval < now()` evaluated by Postgres, so those cases belong here as well.

Throwaway data uses the 998_6xx range (single-session, never committed).
"""

import datetime as dt

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.events.user_cleanup import (
    DELETION_REQUESTED_USERS_SELECT_STATEMENT,
    DEMOTABLE_LEFT_USERS_SELECT_STATEMENT,
    INACTIVE_USERS_SELECT_STATEMENT,
    LEFT_STATUS_GRACE_INTERVAL,
)
from mitup_bot.models import JoinedUsers, Meetup, Settings, User
from mitup_bot.models.users import UserStatus

pytestmark = pytest.mark.db_test

GRACE_DAYS = int(LEFT_STATUS_GRACE_INTERVAL.split()[0])


def days_ago(days: int) -> dt.datetime:
    return dt.datetime.now(dt.UTC) - dt.timedelta(days=days)


def make_user(tg_user_id: int, *, status: UserStatus = UserStatus.LEFT, left_time: dt.datetime | None = None) -> User:
    return User(
        first_name=f"Retention {tg_user_id}",
        tg_user_id=tg_user_id,
        status=status,
        left_time=left_time,
        settings=Settings(),
    )


def make_meetup(owner: User, *, active: bool) -> Meetup:
    return Meetup(
        title="Retention Meeting",
        waiting_list=False,
        public=False,
        allow_invitation=False,
        incognito=False,
        owner=owner,
        active=active,
    )


async def inactive_user_ids(db_session: AsyncSession) -> set[int]:
    return set((await db_session.exec(INACTIVE_USERS_SELECT_STATEMENT)).all())


async def demotable_user_ids(db_session: AsyncSession) -> set[int]:
    return {user.db_id for user in (await db_session.exec(DEMOTABLE_LEFT_USERS_SELECT_STATEMENT)).all()}


async def join_active_meeting(db_session: AsyncSession, joiner: User, host: User):
    """Give `joiner` a link to an active meeting owned by `host` — the tie that turns a purge into
    a demotion."""
    active_meeting = make_meetup(host, active=True)
    db_session.add(active_meeting)
    await db_session.flush()
    db_session.add(JoinedUsers(user=joiner, meetup=active_meeting))
    await db_session.flush()


async def test_left_user_with_active_owned_meeting_is_retained(db_session: AsyncSession):
    """A LEFT user who still owns an active meeting is not selected — deleting them would cascade
    the meeting away and silently strand its participants."""
    owner = make_user(998_601)
    db_session.add(owner)
    await db_session.flush()
    db_session.add(make_meetup(owner, active=True))
    await db_session.flush()

    assert owner.id not in await inactive_user_ids(db_session)


async def test_left_user_with_active_joined_meeting_is_retained(db_session: AsyncSession):
    """A LEFT user who joined an active meeting is not selected — deleting them would drop their
    participation and kick them out of a meeting they never left."""
    host = make_user(998_602, status=UserStatus.MEMBER)
    joiner = make_user(998_603)
    db_session.add_all([host, joiner])
    await db_session.flush()
    active_meeting = make_meetup(host, active=True)
    db_session.add(active_meeting)
    await db_session.flush()
    db_session.add(JoinedUsers(user=joiner, meetup=active_meeting))
    await db_session.flush()

    assert joiner.id not in await inactive_user_ids(db_session)


async def test_left_user_with_only_inactive_meetings_is_selected(db_session: AsyncSession):
    """A LEFT user whose owned and joined meetings are all inactive is safe to purge."""
    host = make_user(998_604, status=UserStatus.MEMBER)
    subject = make_user(998_605)
    db_session.add_all([host, subject])
    await db_session.flush()
    own_inactive = make_meetup(subject, active=False)
    joined_inactive = make_meetup(host, active=False)
    db_session.add_all([own_inactive, joined_inactive])
    await db_session.flush()
    db_session.add(JoinedUsers(user=subject, meetup=joined_inactive))
    await db_session.flush()

    assert subject.id in await inactive_user_ids(db_session)


async def test_left_user_with_no_meetings_is_selected(db_session: AsyncSession):
    """A LEFT user with no meetings at all is selected — the base case, unchanged by the guards."""
    subject = make_user(998_606)
    db_session.add(subject)
    await db_session.flush()

    assert subject.id in await inactive_user_ids(db_session)


async def test_invited_left_user_is_never_selected(db_session: AsyncSession):
    """Invited (outside) users (tg_user_id == -1) stay out of user_cleanup even when LEFT with no
    meetings — inactive_meetings owns their lifecycle."""
    invited = make_user(-1)
    db_session.add(invited)
    await db_session.flush()

    assert invited.id not in await inactive_user_ids(db_session)


async def test_left_user_past_grace_with_only_a_join_link_is_demotable(db_session: AsyncSession):
    """The case the demotion exists for: nothing but a join link to an active meeting keeps this
    user's row alive, so they lose the account and keep the slot."""
    host = make_user(998_610, status=UserStatus.MEMBER)
    subject = make_user(998_611, left_time=days_ago(GRACE_DAYS + 1))
    db_session.add_all([host, subject])
    await db_session.flush()
    db_session.add(make_meetup(subject, active=False))
    await join_active_meeting(db_session, subject, host)

    assert subject.id in await demotable_user_ids(db_session)
    # The join link keeps the same user out of the purge, which is what makes the two passes
    # mutually exclusive.
    assert subject.id not in await inactive_user_ids(db_session)


async def test_left_user_within_grace_is_not_demotable(db_session: AsyncSession):
    """Inside the grace period the user is kept whole: someone who blocked the bot last week may
    still come back and find their meetings."""
    host = make_user(998_612, status=UserStatus.MEMBER)
    subject = make_user(998_613, left_time=days_ago(GRACE_DAYS - 1))
    db_session.add_all([host, subject])
    await db_session.flush()
    await join_active_meeting(db_session, subject, host)

    assert subject.id not in await demotable_user_ids(db_session)


async def test_left_user_with_active_owned_meeting_is_not_demotable(db_session: AsyncSession):
    """An active owned meeting outranks the grace period — those meetings deactivate on their own
    schedule first, and only then is the user considered."""
    host = make_user(998_614, status=UserStatus.MEMBER)
    subject = make_user(998_615, left_time=days_ago(GRACE_DAYS + 1))
    db_session.add_all([host, subject])
    await db_session.flush()
    db_session.add(make_meetup(subject, active=True))
    await join_active_meeting(db_session, subject, host)

    assert subject.id not in await demotable_user_ids(db_session)


async def test_left_user_with_no_link_to_an_active_meeting_is_purged_not_demoted(db_session: AsyncSession):
    """With nobody depending on them the grace period changes nothing: this user is purged, and the
    demotion must not claim them first."""
    subject = make_user(998_616, left_time=days_ago(GRACE_DAYS + 1))
    db_session.add(subject)
    await db_session.flush()
    db_session.add(make_meetup(subject, active=False))
    await db_session.flush()

    assert subject.id not in await demotable_user_ids(db_session)
    assert subject.id in await inactive_user_ids(db_session)


async def test_left_user_without_a_left_time_is_not_demotable(db_session: AsyncSession):
    """A NULL stamp never satisfies the grace comparison, so a LEFT row that carries none is kept
    whole rather than demoted on an unknown clock."""
    host = make_user(998_617, status=UserStatus.MEMBER)
    subject = make_user(998_618)
    db_session.add_all([host, subject])
    await db_session.flush()
    await join_active_meeting(db_session, subject, host)

    assert subject.left_time is None
    assert subject.id not in await demotable_user_ids(db_session)


async def test_deletion_requested_user_is_selected_despite_active_meeting(db_session: AsyncSession):
    """A privacy erasure proceeds unconditionally: a DELETION_REQUESTED user is selected by the
    farewell statement even while owning an active meeting."""
    subject = make_user(998_607, status=UserStatus.DELETION_REQUESTED)
    db_session.add(subject)
    await db_session.flush()
    db_session.add(make_meetup(subject, active=True))
    await db_session.flush()

    marked = (await db_session.exec(DELETION_REQUESTED_USERS_SELECT_STATEMENT)).all()
    assert subject.id in {user.id for user in marked}
    # The retention guards keep this same user out of the outright-purge statement.
    assert subject.id not in await inactive_user_ids(db_session)
