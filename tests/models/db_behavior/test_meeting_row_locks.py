"""Concurrency tests for the per-meeting FOR UPDATE row lock (issue #187).

Each actor runs the same critical section the handlers run — lock the meetup row via
``Meetup.by_id(for_update=True)``, read capacity/waiting-list state, mutate — in its own
``db.begin()`` transaction, against real Postgres. Mock sessions cannot catch these races.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import db
from mitup_bot.models import JoinedUsers, Meetup, Settings, User
from mitup_bot.models.joined_users import JOINED_USERS_UNIQUE_CONSTRAINT
from tests.helpers.locking import RACE_TIMEOUT, race

pytestmark = pytest.mark.db_test


@contextlib.asynccontextmanager
async def _provisioned_meeting(
    tg_base: int,
    *,
    max_members: int | None,
    waiting_list: bool,
    users: int = 0,
    participants: tuple[int, ...] = (),
    waiting: tuple[int, ...] = (),
) -> AsyncIterator[int]:
    """Provision a meeting committed to the database and tear it down afterwards.

    Committed data is required here: the conftest seeds live in the session fixture's open
    transaction and are invisible to the concurrent ``db.begin()`` sessions these tests race.
    ``participants``/``waiting`` list the user offsets whose membership rows pre-exist.
    """
    async with db.begin() as session:
        # Offset 0 is the owner, offsets 1..users are extra users; each test claims its own
        # tg range under 997_0xx — the committed cross-session range from the data collision
        # rule in the db-integration reference.
        owner = User(first_name="Lock Owner", tg_user_id=tg_base, settings=Settings())
        extras = [
            User(first_name=f"Lock User {offset}", tg_user_id=tg_base + offset, settings=Settings())
            for offset in range(1, users + 1)
        ]
        meetup = Meetup(
            title="Row Lock Meeting",
            waiting_list=waiting_list,
            public=False,
            allow_invitation=False,
            incognito=False,
            max_members=max_members,
            owner=owner,
        )
        session.add(owner)
        session.add_all(extras)
        session.add(meetup)
        await session.flush()
        assert meetup.id is not None
        meetup_id = meetup.id
        user_ids = {offset: extras[offset - 1].id for offset in range(1, users + 1)}
        session.add_all(
            JoinedUsers(user_id=user_ids[offset], meetup_id=meetup_id, is_waiting_list=False) for offset in participants
        )
    try:
        # One transaction per waiting entry: the created_time trigger stamps CURRENT_TIMESTAMP
        # (fixed per transaction) and promotion order sorts on created_time.
        for offset in waiting:
            async with db.begin() as session:
                session.add(JoinedUsers(user_id=user_ids[offset], meetup_id=meetup_id, is_waiting_list=True))
        yield meetup_id
    finally:
        async with db.begin() as session:
            await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text("DELETE FROM meetups WHERE id = :mid").bindparams(mid=meetup_id)
            )
            await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text("DELETE FROM users WHERE tg_user_id BETWEEN :lo AND :hi").bindparams(
                    lo=tg_base, hi=tg_base + users
                )
            )


async def _join(session: AsyncSession, meetup_id: int, tg_user_id: int) -> bool:
    """The join critical section as handle_join_leave_operation runs it: lock the meetup row,
    read capacity, insert through the racy_flush savepoint. True when a membership row landed
    (participant or waiting), False when the join was rejected or the meeting vanished."""
    user = (await session.exec(select(User).where(User.tg_user_id == tg_user_id))).one()
    await session.refresh(user, ["joined_links"])
    meeting = await Meetup.by_id(session, meetup_id, for_update=True)
    if meeting is None or not meeting.active:
        return False
    if user.joined_meeting(meeting.db_id):
        return False
    if not meeting.join_allowed():
        return False
    joined_link = await db.racy_flush(
        session, lambda: meeting.add_participant(user), constraint=JOINED_USERS_UNIQUE_CONSTRAINT
    )
    return joined_link is not None


async def _remove_participant(session: AsyncSession, meetup_id: int, tg_user_id: int) -> list[int]:
    """The leave/kick-out critical section: lock, find the membership, remove it (which promotes
    from the waiting list) and flush. Returns the tg ids of the promoted users."""
    user = (await session.exec(select(User).where(User.tg_user_id == tg_user_id))).one()
    meeting = await Meetup.by_id(session, meetup_id, for_update=True)
    assert meeting is not None
    joined_link = meeting.participant(user.db_id)
    assert joined_link is not None
    promoted = meeting.remove_participant(joined_link)
    await session.flush()
    return [link.user.tg_user_id for link in promoted]


async def _delete_meeting(session: AsyncSession, meetup_id: int) -> bool:
    """The delete-confirm critical section: locked re-load, then delete (memberships cascade).
    False when the row is already gone — the handler's nothing-left-to-do branch."""
    meeting = await Meetup.by_id(session, meetup_id, for_update=True)
    if meeting is None:
        return False
    await session.delete(meeting)
    return True


async def _set_max_members(session: AsyncSession, meetup_id: int, max_members: int | None) -> None:
    """The capacity-edit critical section as edit_meeting_max_participants runs it: locked
    re-load, mutate max_members, flush."""
    meeting = await Meetup.by_id(session, meetup_id, for_update=True)
    assert meeting is not None
    meeting.max_members = max_members
    await session.flush()


async def _committed_state(meetup_id: int) -> tuple[set[int], set[int]]:
    """(participant tg ids, waiting tg ids) as visible to a fresh transaction after the race."""
    async with db.begin() as session:
        meeting = await Meetup.by_id(session, meetup_id)
        assert meeting is not None
        participant_tgs = {link.user.tg_user_id for link in meeting.joined_links if not link.is_waiting_list}
        waiting_tgs = {link.user.tg_user_id for link in meeting.joined_links if link.is_waiting_list}
    return participant_tgs, waiting_tgs


@pytest.mark.parametrize("waiting_list", [True, False], ids=["overflow_to_waiting_list", "second_join_rejected"])
async def test_double_join_on_last_slot_serializes(db_session: AsyncSession, waiting_list: bool):
    """Two users race for the single free slot: the row lock serializes the capacity reads, so
    exactly one becomes a participant and the loser overflows to the waiting list (when enabled)
    or is rejected. Without the lock both read "not full" and both become participants."""
    tg_base = 997_010 if waiting_list else 997_015
    async with _provisioned_meeting(tg_base, max_members=1, waiting_list=waiting_list, users=2) as meetup_id:
        first_join, second_join = await race(
            lambda session: _join(session, meetup_id, tg_base + 1),
            lambda session: _join(session, meetup_id, tg_base + 2),
        )

        assert first_join is True
        # Waiting list on: the second join landed a waiting row; off: add_participant returned
        # None once the locked load showed the meeting full.
        assert second_join is waiting_list

        participant_tgs, waiting_tgs = await _committed_state(meetup_id)
        assert participant_tgs == {tg_base + 1}
        assert waiting_tgs == ({tg_base + 2} if waiting_list else set())


async def test_leave_racing_join_promotes_exactly_once(db_session: AsyncSession):
    """A leave that promotes from the waiting list races a fresh join: the leave (lock holder)
    promotes the seeded waiter, and the join — serialized behind it — sees the meeting full again
    and lands in the waiting list. Exactly one promotion, no capacity overrun."""
    tg_base = 997_020
    async with _provisioned_meeting(
        tg_base, max_members=1, waiting_list=True, users=3, participants=(1,), waiting=(2,)
    ) as meetup_id:
        promoted_tgs, join_result = await race(
            lambda session: _remove_participant(session, meetup_id, tg_base + 1),
            lambda session: _join(session, meetup_id, tg_base + 3),
        )

        assert promoted_tgs == [tg_base + 2]  # exactly one promotion: the seeded waiter
        assert join_result is True  # joined the (again full) meeting's waiting list

        participant_tgs, waiting_tgs = await _committed_state(meetup_id)
        assert participant_tgs == {tg_base + 2}
        assert waiting_tgs == {tg_base + 3}


async def test_kickout_racing_join_stays_consistent(db_session: AsyncSession):
    """The reverse interleaving of the leave test: the join holds the lock first (landing in the
    waiting list behind the seeded waiter), then the kick-out removes the participant and must
    promote the oldest waiting entry — the seeded waiter, not the racer that just joined."""
    tg_base = 997_030
    async with _provisioned_meeting(
        tg_base, max_members=1, waiting_list=True, users=3, participants=(1,), waiting=(2,)
    ) as meetup_id:
        join_result, promoted_tgs = await race(
            lambda session: _join(session, meetup_id, tg_base + 3),
            lambda session: _remove_participant(session, meetup_id, tg_base + 1),
        )

        assert join_result is True
        assert promoted_tgs == [tg_base + 2]  # promotion picked the oldest waiting entry

        participant_tgs, waiting_tgs = await _committed_state(meetup_id)
        assert participant_tgs == {tg_base + 2}
        assert waiting_tgs == {tg_base + 3}


async def test_capacity_increase_racing_join_admits_under_new_cap(db_session: AsyncSession):
    """A max_members increase races a join on a currently full meeting: the join, serialized
    behind the capacity edit, is admitted as a participant under the new cap. Neither write is
    lost — the committed row holds the new capacity and both participants."""
    tg_base = 997_060
    async with _provisioned_meeting(tg_base, max_members=1, waiting_list=True, users=2, participants=(1,)) as meetup_id:
        _, join_result = await race(
            lambda session: _set_max_members(session, meetup_id, 2),
            lambda session: _join(session, meetup_id, tg_base + 2),
        )

        assert join_result is True  # admitted under the raised cap, not waiting-listed

        async with db.begin() as session:
            meeting = await Meetup.by_id(session, meetup_id)
            assert meeting is not None
            assert meeting.max_members == 2  # the capacity edit was not lost to the join's write
            assert {link.user.tg_user_id for link in meeting.participants} == {tg_base + 1, tg_base + 2}
            assert meeting.n_waiting == 0


async def test_capacity_decrease_racing_join_overflows_to_waiting(db_session: AsyncSession):
    """A max_members decrease down to the current participant count races a join: the join,
    serialized behind the edit, finds the meeting already full under the new cap and overflows to
    the waiting list — the final state is never over capacity."""
    tg_base = 997_070
    async with _provisioned_meeting(tg_base, max_members=2, waiting_list=True, users=2, participants=(1,)) as meetup_id:
        _, join_result = await race(
            lambda session: _set_max_members(session, meetup_id, 1),
            lambda session: _join(session, meetup_id, tg_base + 2),
        )

        assert join_result is True  # landed in the waiting list under the lowered cap

        async with db.begin() as session:
            meeting = await Meetup.by_id(session, meetup_id)
            assert meeting is not None
            assert meeting.max_members == 1  # the capacity edit was not lost to the join's write
            assert {link.user.tg_user_id for link in meeting.participants} == {tg_base + 1}
            assert {link.user.tg_user_id for link in meeting.waiting_links()} == {tg_base + 2}


@pytest.mark.parametrize("delete_goes_first", [True, False], ids=["delete_first", "join_first"])
async def test_delete_racing_join_leaves_no_orphans(db_session: AsyncSession, delete_goes_first: bool):
    """Delete-confirm's locked re-load racing a join: either the delete wins and the join finds
    the row gone, or the join lands first and the delete's re-load sees the fresh membership and
    removes it. Either way no membership row survives the meeting."""
    tg_base = 997_040 if delete_goes_first else 997_045
    async with _provisioned_meeting(tg_base, max_members=None, waiting_list=False, users=1) as meetup_id:
        if delete_goes_first:
            delete_result, join_result = await race(
                lambda session: _delete_meeting(session, meetup_id),
                lambda session: _join(session, meetup_id, tg_base + 1),
            )
            assert delete_result is True
            assert join_result is False  # the locked load found no row after the delete committed
        else:
            join_result, delete_result = await race(
                lambda session: _join(session, meetup_id, tg_base + 1),
                lambda session: _delete_meeting(session, meetup_id),
            )
            assert join_result is True  # landed before the delete took the lock
            assert delete_result is True  # the locked re-load saw the fresh membership and removed it

        async with db.begin() as session:
            orphan_links = (
                await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                    text("SELECT count(*) FROM joined_users WHERE meetup_id = :mid").bindparams(mid=meetup_id)
                )
            ).scalar_one()
            meetup_rows = (
                await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                    text("SELECT count(*) FROM meetups WHERE id = :mid").bindparams(mid=meetup_id)
                )
            ).scalar_one()
            joiner_rows = (
                await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                    text("SELECT count(*) FROM users WHERE tg_user_id = :tg").bindparams(tg=tg_base + 1)
                )
            ).scalar_one()
        assert orphan_links == 0
        assert meetup_rows == 0
        assert joiner_rows == 1  # the joiner's user row must survive the meeting's deletion


async def test_locked_reload_refreshes_identity_mapped_meeting(db_session: AsyncSession):
    """Regression guard for populate_existing on the locked load: a session that already holds the
    meeting in its identity map (via any earlier unlocked read) must see post-lock reality — a
    membership committed by another session — on the SAME object after by_id(for_update=True).
    Without populate_existing the locked SELECT returns the stale pre-lock state."""
    tg_base = 997_050
    async with _provisioned_meeting(tg_base, max_members=None, waiting_list=False, users=1) as meetup_id:
        async with asyncio.timeout(RACE_TIMEOUT), db.begin() as observer:
            stale = await Meetup.by_id(observer, meetup_id)
            assert stale is not None
            assert stale.n_participants == 0

            async with db.begin() as writer:
                assert await _join(writer, meetup_id, tg_base + 1) is True

            locked = await Meetup.by_id(observer, meetup_id, for_update=True)
            assert locked is stale  # identity map returns the same object, not a new instance
            assert locked.n_participants == 1  # 0 here means populate_existing was dropped
