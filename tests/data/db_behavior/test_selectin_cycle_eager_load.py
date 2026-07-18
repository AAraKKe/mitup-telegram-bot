"""Regression: `User.by_tg_user_id(load_participants=True)` eager-loads the leaves the view reads.

Relationship-level `lazy="selectin"` does not cascade through a load-path cycle: once the eager-load
path reaches a mapper already on it (User -> Meetup -> JoinedUsers -> User), SQLAlchemy stops implicit
eager loading at that hop. So a user-rooted load leaves each meeting's `owner`, `joined_links[].user`
and `joined_links[].invited_by` unloaded unless the loader chains spell them out. The full-card
renderers (`views/meeting_text.py`) read exactly those in plain Python; under the async engine an
unloaded access raises `MissingGreenlet`. `load_participants=True` opts a call into those chains; the
default deliberately stops short (see the contract test) so ordinary handlers don't pay for the graph.

Each test seeds in one committed transaction and reads back in a FRESH session: a same-session read is
served from the identity map (collections already populated by the flush) and would mask the bug.
"""

from typing import Any, cast

import pytest
from sqlalchemy.orm import QueryableAttribute, selectinload
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import db
from mitup_bot.models import JoinedUsers, Meetup, Settings, User

pytestmark = pytest.mark.db_test

OWNER_TG_USER_ID = 997_201
PARTICIPANT_TG_USER_ID = 997_202
INVITER_TG_USER_ID = 997_203


async def seed_meeting_with_invited_participant(db_session: AsyncSession) -> None:
    """Owner owns a meeting; participant joined it, invited by a third user.

    The invitation is load-bearing: `participant_name` only traverses `invited_by` when it is set, so
    an invited participant is what exercises the `invited_by` leaf of the eager-load chains.

    Idempotent so every test can call it: the shared session-scoped database persists rows across
    tests, and re-seeding would leave `by_tg_user_id`'s `.first()` reading an arbitrary duplicate.
    """
    async with db.begin() as session:
        if await User.by_tg_user_id(session, OWNER_TG_USER_ID, load_collections=False) is not None:
            return
        owner = User(first_name="Cycle Owner", tg_user_id=OWNER_TG_USER_ID, settings=Settings())
        participant = User(first_name="Cycle Participant", tg_user_id=PARTICIPANT_TG_USER_ID, settings=Settings())
        inviter = User(first_name="Cycle Inviter", tg_user_id=INVITER_TG_USER_ID, settings=Settings())
        meeting = Meetup(
            title="Cycle Meeting",
            waiting_list=False,
            public=False,
            allow_invitation=True,
            incognito=False,
            owner=owner,
        )
        session.add_all([owner, participant, inviter, meeting])
        await session.flush()
        session.add(JoinedUsers(user=participant, meetup=meeting, invited_by=inviter))


async def test_owner_rooted_load_reaches_participant_leaves(db_session: AsyncSession):
    """From the owner's `meetups`, the deep load reaches owner, participant, and inviter names."""
    await seed_meeting_with_invited_participant(db_session)

    async with db.begin() as fresh:
        owner = await User.by_tg_user_id(fresh, OWNER_TG_USER_ID, must_exist=True, load_participants=True)
        meeting = owner.meetups[0]

        # The reads mirror views/meeting_text.py: meeting header, then each participant line.
        assert meeting.owner.display_name == "Cycle Owner"
        link = meeting.joined_links[0]
        assert link.user.display_name == "Cycle Participant"
        assert link.invited_by is not None
        assert link.invited_by.inline_name == "Cycle Inviter"


async def test_participant_rooted_load_reaches_participant_leaves(db_session: AsyncSession):
    """From the participant's `joined_links`, the deep load fully loads the joined meeting's list."""
    await seed_meeting_with_invited_participant(db_session)

    async with db.begin() as fresh:
        participant = await User.by_tg_user_id(fresh, PARTICIPANT_TG_USER_ID, must_exist=True, load_participants=True)
        meeting = participant.joined_links[0].meetup

        assert meeting.title == "Cycle Meeting"
        # owner is a DIFFERENT user than the loaded one here, so it is not rescued by the many-to-one
        # identity-map fallback: this asserts the explicit `meetup -> owner` chain actually loads it.
        assert meeting.owner.display_name == "Cycle Owner"
        link = meeting.joined_links[0]
        assert link.user.display_name == "Cycle Participant"
        assert link.invited_by is not None
        assert link.invited_by.inline_name == "Cycle Inviter"


async def test_default_load_leaves_participant_leaves_unloaded(db_session: AsyncSession):
    """Contract: WITHOUT `load_participants` the deep leaves stay unloaded, so the default does not
    silently pull the whole social graph. `joined_links -> meetup` is still named (the list screens
    read `link.meetup.title`), but that meeting's own `owner` and `joined_links` remain unloaded."""
    await seed_meeting_with_invited_participant(db_session)

    async with db.begin() as fresh:
        participant = await User.by_tg_user_id(fresh, PARTICIPANT_TG_USER_ID, must_exist=True)
        joined_meeting = participant.joined_links[0].meetup

        # The default reaches the meeting itself (its scalar columns render the joined-meetings list)...
        assert joined_meeting.title == "Cycle Meeting"
        # ...but stops before the participant leaves the deep flag would add.
        loaded = db.loaded_attributes(joined_meeting)
        assert "owner" not in loaded
        assert "joined_links" not in loaded


async def test_load_options_are_generative(db_session: AsyncSession):
    """Proof that reusing one root `Load` for several leaf chains is safe: SQLAlchemy loader options
    are generative, so each `selectinload` returns an independent option. If a shared root mutated,
    at least one branch below would be lost and its access would raise `MissingGreenlet`."""
    await seed_meeting_with_invited_participant(db_session)

    def q(attr: Any) -> QueryableAttribute[Any]:
        return cast("QueryableAttribute[Any]", attr)

    async with db.begin() as fresh:
        root = selectinload(q(User.meetups))
        joined_links_leaf = root.selectinload(q(Meetup.joined_links))
        statement = (
            select(User)
            .where(User.tg_user_id == OWNER_TG_USER_ID)
            .options(
                root.selectinload(q(Meetup.owner)),
                joined_links_leaf.selectinload(q(JoinedUsers.user)),
                joined_links_leaf.selectinload(q(JoinedUsers.invited_by)),
            )
        )
        meeting = (await fresh.exec(statement)).one().meetups[0]

        # Inspect before touching the leaves: the same reuse `user_collection_loaders` relies on must
        # have eagerly loaded every branch off the shared root, not fallen back to a (forbidden) lazy load.
        assert {"owner", "joined_links"} <= db.loaded_attributes(meeting)

        assert meeting.owner.display_name == "Cycle Owner"
        assert meeting.joined_links[0].user.display_name == "Cycle Participant"
        assert meeting.joined_links[0].invited_by is not None
