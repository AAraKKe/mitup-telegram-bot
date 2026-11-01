"""DB-integration tests for the cleanup queries of users who are not members.

A mock session cannot model the age and `NOT EXISTS` predicates of the JOINED_ONLY and abandoned
onboarding purges, so these cases run the production statements on real SQL.
"""

import datetime as dt

import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from mitup_bot.events import inactive_meetings
from mitup_bot.models import JoinedUsers, Meetup, Settings, User
from mitup_bot.models.users import UserStatus

pytestmark = pytest.mark.db_test

JOINED_ONLY_STATEMENT = inactive_meetings.JOINED_ONLY_WITHOUT_ACTIVE_LINKS_STATEMENT
ONBOARDING_STATEMENT = inactive_meetings.ABANDONED_ONBOARDING_STATEMENT
ONE_DAY = dt.timedelta(days=1)
PAST_THE_ANSWER_WINDOW = dt.datetime.now(dt.UTC) - inactive_meetings.ONBOARDING_ANSWER_WINDOW - ONE_DAY
INSIDE_THE_ANSWER_WINDOW = dt.datetime.now(dt.UTC) - inactive_meetings.ONBOARDING_ANSWER_WINDOW + ONE_DAY


async def run_cleanup(
    db_session: AsyncSession,
    statement: SelectOfScalar[int] | SelectOfScalar[None] = JOINED_ONLY_STATEMENT,
    status: UserStatus = UserStatus.JOINED_ONLY,
) -> int:
    """Run a production purge inside the test's open transaction; returns how many rows it took."""
    nominated = list((await db_session.exec(statement)).all())
    taken = await inactive_meetings.delete_users_still_in_status(db_session, nominated, status)
    await db_session.flush()
    return len(taken)


async def test_joined_only_deleted_when_last_meeting_inactive(db_session: AsyncSession):
    """A JOINED_ONLY user with a single inline-joined (now-inactive) meeting is deleted.

    No remaining active-meeting link → the NOT EXISTS subquery matches → DELETE
    picks them up.
    """
    owner = User(first_name="Owner Single", tg_user_id=998_500, status=UserStatus.MEMBER, settings=Settings())
    db_session.add(owner)
    await db_session.flush()

    joined_only = User(
        first_name="Joined Only Single", tg_user_id=998_501, status=UserStatus.JOINED_ONLY, settings=Settings()
    )
    db_session.add(joined_only)
    await db_session.flush()

    inactive_meeting = Meetup(
        title="Single Inactive Meeting",
        waiting_list=False,
        public=False,
        allow_invitation=False,
        incognito=False,
        owner=owner,
        active=False,
    )
    db_session.add(inactive_meeting)
    await db_session.flush()
    db_session.add(JoinedUsers(user=joined_only, meetup=inactive_meeting))
    await db_session.flush()

    joined_only_id = joined_only.id

    deleted_count = await run_cleanup(db_session)

    assert deleted_count == 1
    surviving = (await db_session.exec(select(User).where(User.id == joined_only_id))).first()
    assert surviving is None


async def test_joined_only_preserved_when_another_meeting_is_active(db_session: AsyncSession):
    """A JOINED_ONLY user with at least one ACTIVE meeting survives the cleanup.

    The NOT EXISTS subquery finds the still-active meeting B, so the user is kept.
    Critically: the second meeting becoming inactive is a separate cron run — this
    test pins the multi-meeting JOINED_ONLY invariant for the first deactivation.
    """
    owner_a = User(first_name="Owner A", tg_user_id=998_510, status=UserStatus.MEMBER, settings=Settings())
    owner_b = User(first_name="Owner B", tg_user_id=998_511, status=UserStatus.MEMBER, settings=Settings())
    db_session.add_all([owner_a, owner_b])
    await db_session.flush()

    joined_only = User(
        first_name="Joined Only Multi", tg_user_id=998_512, status=UserStatus.JOINED_ONLY, settings=Settings()
    )
    db_session.add(joined_only)
    await db_session.flush()

    inactive_meeting = Meetup(
        title="Inactive Meeting A",
        waiting_list=False,
        public=False,
        allow_invitation=False,
        incognito=False,
        owner=owner_a,
        active=False,
    )
    active_meeting = Meetup(
        title="Active Meeting B",
        waiting_list=False,
        public=False,
        allow_invitation=False,
        incognito=False,
        owner=owner_b,
        active=True,
    )
    db_session.add_all([inactive_meeting, active_meeting])
    await db_session.flush()
    db_session.add(JoinedUsers(user=joined_only, meetup=inactive_meeting))
    db_session.add(JoinedUsers(user=joined_only, meetup=active_meeting))
    await db_session.flush()

    joined_only_id = joined_only.id

    deleted_count = await run_cleanup(db_session)

    assert deleted_count == 0
    surviving = (await db_session.exec(select(User).where(User.id == joined_only_id))).first()
    assert surviving is not None
    assert surviving.status is UserStatus.JOINED_ONLY


async def test_joined_only_deleted_when_second_meeting_also_inactive(db_session: AsyncSession):
    """The second leg of the multi-meeting case: once meeting B also goes inactive, the JOINED_ONLY user is deleted.

    Pins the full lifecycle: A inactive → user survives → B inactive → user deleted.
    """
    owner_a = User(first_name="Owner A2", tg_user_id=998_520, status=UserStatus.MEMBER, settings=Settings())
    owner_b = User(first_name="Owner B2", tg_user_id=998_521, status=UserStatus.MEMBER, settings=Settings())
    db_session.add_all([owner_a, owner_b])
    await db_session.flush()

    joined_only = User(
        first_name="Joined Only Final", tg_user_id=998_522, status=UserStatus.JOINED_ONLY, settings=Settings()
    )
    db_session.add(joined_only)
    await db_session.flush()

    inactive_a = Meetup(
        title="Inactive A",
        waiting_list=False,
        public=False,
        allow_invitation=False,
        incognito=False,
        owner=owner_a,
        active=False,
    )
    inactive_b = Meetup(
        title="Inactive B",
        waiting_list=False,
        public=False,
        allow_invitation=False,
        incognito=False,
        owner=owner_b,
        active=False,
    )
    db_session.add_all([inactive_a, inactive_b])
    await db_session.flush()
    db_session.add(JoinedUsers(user=joined_only, meetup=inactive_a))
    db_session.add(JoinedUsers(user=joined_only, meetup=inactive_b))
    await db_session.flush()

    joined_only_id = joined_only.id

    deleted_count = await run_cleanup(db_session)

    assert deleted_count == 1
    surviving = (await db_session.exec(select(User).where(User.id == joined_only_id))).first()
    assert surviving is None


def unfinished_user(tg_user_id: int, status: UserStatus, created_time: dt.datetime) -> User:
    return User(
        first_name="Unfinished",
        tg_user_id=tg_user_id,
        status=status,
        created_time=created_time,
        settings=Settings(),
    )


def meeting_owned_by(owner: User, *, active: bool) -> Meetup:
    return Meetup(
        title="Owned Meeting",
        waiting_list=False,
        public=False,
        allow_invitation=False,
        incognito=False,
        owner=owner,
        active=active,
    )


@pytest.mark.parametrize(
    ("tg_user_id", "created_time", "purged_count"),
    [(998_530, INSIDE_THE_ANSWER_WINDOW, 0), (998_531, PAST_THE_ANSWER_WINDOW, 1)],
    ids=["inside_the_window", "past_the_window"],
)
async def test_abandoned_onboarding_is_purged_only_past_the_answer_window(
    db_session: AsyncSession,
    tg_user_id: int,
    created_time: dt.datetime,
    purged_count: int,
):
    db_session.add(unfinished_user(tg_user_id, UserStatus.ONBOARDING, created_time))
    await db_session.flush()

    assert await run_cleanup(db_session, ONBOARDING_STATEMENT, UserStatus.ONBOARDING) == purged_count


@pytest.mark.parametrize(
    ("status", "statement", "active"),
    [
        (UserStatus.JOINED_ONLY, JOINED_ONLY_STATEMENT, True),
        (UserStatus.JOINED_ONLY, JOINED_ONLY_STATEMENT, False),
        (UserStatus.ONBOARDING, ONBOARDING_STATEMENT, True),
        (UserStatus.ONBOARDING, ONBOARDING_STATEMENT, False),
    ],
    ids=["joined_only_active", "joined_only_inactive", "onboarding_active", "onboarding_inactive"],
)
async def test_owner_of_a_meeting_survives_the_cleanup(
    db_session: AsyncSession,
    status: UserStatus,
    statement: SelectOfScalar[int] | SelectOfScalar[None],
    active: bool,
):
    """The meeting cascades from its owner, so purging the row would delete it for everyone in it."""
    owner = unfinished_user(998_540, status, PAST_THE_ANSWER_WINDOW)
    db_session.add(owner)
    await db_session.flush()
    db_session.add(meeting_owned_by(owner, active=active))
    await db_session.flush()

    assert await run_cleanup(db_session, statement, status) == 0


async def test_abandoned_onboarding_with_an_active_meeting_joined_survives(db_session: AsyncSession):
    owner = User(first_name="Host", tg_user_id=998_550, status=UserStatus.MEMBER, settings=Settings())
    joiner = unfinished_user(998_551, UserStatus.ONBOARDING, PAST_THE_ANSWER_WINDOW)
    db_session.add_all([owner, joiner])
    await db_session.flush()
    meeting = meeting_owned_by(owner, active=True)
    db_session.add(meeting)
    await db_session.flush()
    db_session.add(JoinedUsers(user=joiner, meetup=meeting))
    await db_session.flush()

    assert await run_cleanup(db_session, ONBOARDING_STATEMENT, UserStatus.ONBOARDING) == 0


async def test_joined_only_cleanup_leaves_a_fresh_onboarding_row_alone(db_session: AsyncSession):
    db_session.add(unfinished_user(998_560, UserStatus.ONBOARDING, INSIDE_THE_ANSWER_WINDOW))
    await db_session.flush()

    assert await run_cleanup(db_session, JOINED_ONLY_STATEMENT) == 0


async def test_purge_skips_a_user_who_finished_onboarding_after_the_select(db_session: AsyncSession):
    user = unfinished_user(998_570, UserStatus.ONBOARDING, PAST_THE_ANSWER_WINDOW)
    db_session.add(user)
    await db_session.flush()
    nominated = list((await db_session.exec(ONBOARDING_STATEMENT)).all())
    user.set_status(UserStatus.MEMBER)
    await db_session.flush()

    taken = await inactive_meetings.delete_users_still_in_status(db_session, nominated, UserStatus.ONBOARDING)

    assert taken == []


async def test_purge_returns_the_ids_it_took(db_session: AsyncSession):
    user = unfinished_user(998_580, UserStatus.ONBOARDING, PAST_THE_ANSWER_WINDOW)
    db_session.add(user)
    await db_session.flush()
    user_id = user.db_id

    taken = await inactive_meetings.delete_users_still_in_status(db_session, [user_id], UserStatus.ONBOARDING)

    assert taken == [user_id]
