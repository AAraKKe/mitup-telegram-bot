"""DB-integration tests for the JOINED_ONLY cleanup query.

The `inactive_meetings.run` CLI issues a NOT EXISTS subquery at the end of its loop
to delete every JOINED_ONLY user whose remaining `joined_links` are all to inactive
meetings. The behaviour is mechanically determinate from real SQL — a mock session
can't model the `NOT EXISTS … active=true` predicate, so these cases live here.
"""

import pytest
from sqlalchemy import and_, true
from sqlmodel import Session, delete, exists, select

from mitup_bot.models import JoinedUsers, Meetup, Settings, User
from mitup_bot.models.users import UserStatus

pytestmark = pytest.mark.db_test


def run_joined_only_cleanup(db_session: Session) -> int:
    """Replay the CLI's JOINED_ONLY cleanup query against the live session.

    Mirrors the single-DELETE in `inactive_meetings.run`: delete every JOINED_ONLY user
    whose `joined_links` no longer point to an active meeting. Returns the rowcount.
    """
    stmt = delete(User).where(
        and_(
            User.status == UserStatus.JOINED_ONLY,  # ty: ignore[invalid-argument-type]  # https://github.com/astral-sh/ty/issues/2839
            ~exists(
                select(1)
                .select_from(JoinedUsers)
                .join(Meetup)
                .where(
                    and_(
                        JoinedUsers.user_id == User.id,  # ty: ignore[invalid-argument-type]  # https://github.com/astral-sh/ty/issues/2839
                        Meetup.active == true(),  # ty: ignore[invalid-argument-type]  # https://github.com/astral-sh/ty/issues/2839
                    )
                )
            ),
        )
    )
    result = db_session.exec(stmt)  # type: ignore[call-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
    db_session.flush()
    return result.rowcount or 0


def test_joined_only_deleted_when_last_meeting_inactive(db_session: Session):
    """A JOINED_ONLY user with a single inline-joined (now-inactive) meeting is deleted.

    No remaining active-meeting link → the NOT EXISTS subquery matches → DELETE
    picks them up.
    """
    owner = User(first_name="Owner Single", tg_user_id=998_500, status=UserStatus.MEMBER, settings=Settings())
    db_session.add(owner)
    db_session.flush()

    joined_only = User(
        first_name="Joined Only Single", tg_user_id=998_501, status=UserStatus.JOINED_ONLY, settings=Settings()
    )
    db_session.add(joined_only)
    db_session.flush()

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
    db_session.flush()
    db_session.add(JoinedUsers(user=joined_only, meetup=inactive_meeting))
    db_session.flush()

    joined_only_id = joined_only.id

    deleted_count = run_joined_only_cleanup(db_session)

    assert deleted_count == 1
    surviving = db_session.exec(select(User).where(User.id == joined_only_id)).first()
    assert surviving is None


def test_joined_only_preserved_when_another_meeting_is_active(db_session: Session):
    """A JOINED_ONLY user with at least one ACTIVE meeting survives the cleanup.

    The NOT EXISTS subquery finds the still-active meeting B, so the user is kept.
    Critically: the second meeting becoming inactive is a separate cron run — this
    test pins the multi-meeting JOINED_ONLY invariant for the first deactivation.
    """
    owner_a = User(first_name="Owner A", tg_user_id=998_510, status=UserStatus.MEMBER, settings=Settings())
    owner_b = User(first_name="Owner B", tg_user_id=998_511, status=UserStatus.MEMBER, settings=Settings())
    db_session.add_all([owner_a, owner_b])
    db_session.flush()

    joined_only = User(
        first_name="Joined Only Multi", tg_user_id=998_512, status=UserStatus.JOINED_ONLY, settings=Settings()
    )
    db_session.add(joined_only)
    db_session.flush()

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
    db_session.flush()
    db_session.add(JoinedUsers(user=joined_only, meetup=inactive_meeting))
    db_session.add(JoinedUsers(user=joined_only, meetup=active_meeting))
    db_session.flush()

    joined_only_id = joined_only.id

    deleted_count = run_joined_only_cleanup(db_session)

    assert deleted_count == 0
    surviving = db_session.exec(select(User).where(User.id == joined_only_id)).first()
    assert surviving is not None
    assert surviving.status is UserStatus.JOINED_ONLY


def test_joined_only_deleted_when_second_meeting_also_inactive(db_session: Session):
    """The second leg of the multi-meeting case: once meeting B also goes inactive, the JOINED_ONLY user is deleted.

    Pins the full lifecycle: A inactive → user survives → B inactive → user deleted.
    """
    owner_a = User(first_name="Owner A2", tg_user_id=998_520, status=UserStatus.MEMBER, settings=Settings())
    owner_b = User(first_name="Owner B2", tg_user_id=998_521, status=UserStatus.MEMBER, settings=Settings())
    db_session.add_all([owner_a, owner_b])
    db_session.flush()

    joined_only = User(
        first_name="Joined Only Final", tg_user_id=998_522, status=UserStatus.JOINED_ONLY, settings=Settings()
    )
    db_session.add(joined_only)
    db_session.flush()

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
    db_session.flush()
    db_session.add(JoinedUsers(user=joined_only, meetup=inactive_a))
    db_session.add(JoinedUsers(user=joined_only, meetup=inactive_b))
    db_session.flush()

    joined_only_id = joined_only.id

    deleted_count = run_joined_only_cleanup(db_session)

    assert deleted_count == 1
    surviving = db_session.exec(select(User).where(User.id == joined_only_id)).first()
    assert surviving is None
