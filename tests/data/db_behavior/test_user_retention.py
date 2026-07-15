"""DB-integration tests for the LEFT-user retention guards in `user_cleanup`.

`INACTIVE_USERS_SELECT_STATEMENT` only nominates a LEFT user for deletion once no active meeting
depends on them — neither an active meeting they own nor an active meeting they joined. Those
guards are two `NOT EXISTS` subqueries whose truth is decided by real SQL over real rows; a mock
session cannot evaluate them, so the cases live here. `DELETION_REQUESTED_USERS_SELECT_STATEMENT`
carries no such guard — a privacy erasure proceeds regardless of meetings — which is pinned too.

Throwaway data uses the 998_6xx range (single-session, never committed).
"""

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.events.user_cleanup import (
    DELETION_REQUESTED_USERS_SELECT_STATEMENT,
    INACTIVE_USERS_SELECT_STATEMENT,
)
from mitup_bot.models import JoinedUsers, Meetup, Settings, User
from mitup_bot.models.users import UserStatus

pytestmark = pytest.mark.db_test


def make_user(tg_user_id: int, *, status: UserStatus = UserStatus.LEFT) -> User:
    return User(first_name=f"Retention {tg_user_id}", tg_user_id=tg_user_id, status=status, settings=Settings())


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
