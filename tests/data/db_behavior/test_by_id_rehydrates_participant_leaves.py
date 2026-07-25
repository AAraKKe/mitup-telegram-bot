"""Contract: a same-session `Meetup.by_id` reload re-hydrates the participant leaves the cards render.

This pins the load property `guards.meeting` is built on (incident #282). A default user-rooted load
(`User.by_tg_user_id` without `load_participants`) reaches an OWNED meeting through `user.meetups` with
its `joined_links` loaded, but the selectin cascade stops at the `User -> Meetup -> JoinedUsers -> User`
cycle, so each link's `user` and `invited_by` stay unloaded. The guard resolves every meeting through
`Meetup.by_id` instead; that meetup-rooted load repeats no mapper and hydrates those leaves, which is
what keeps rendering off `guards.meeting` from raising `MissingGreenlet` at every call site. A
loader-strategy change that broke this load would reintroduce the incident everywhere without tripping
the user-rooted tests in `test_selectin_cycle_eager_load.py`, which only assert the joined-meeting root.

The reload runs in the SAME session as the user-rooted load: the meetup is already in the identity map
and its `joined_links` collection is already populated, yet `by_id` still hydrates the leaves and returns
the identical instance. Pinned for the plain reload and for the `for_update=True` locking reload.

Reuses the committed seed from `test_selectin_cycle_eager_load.py` (same `997_20x` range, idempotent), so
both modules read the same rows without a range collision.
"""

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import db
from mitup_bot.models import Meetup, User
from tests.data.db_behavior.test_selectin_cycle_eager_load import (
    OWNER_TG_USER_ID,
    seed_meeting_with_invited_participant,
)

pytestmark = pytest.mark.db_test


@pytest.mark.parametrize("for_update", [False, True], ids=["plain", "for_update"])
async def test_by_id_reload_rehydrates_owned_meeting_participant_leaves(db_session: AsyncSession, for_update: bool):
    """A default user-rooted load leaves the owned meeting's participant leaves unloaded; a same-session
    `Meetup.by_id` reload hydrates them and returns the identical instance (plain and locking reload)."""
    await seed_meeting_with_invited_participant(db_session)

    async with db.begin() as fresh:
        owner = await User.by_tg_user_id(fresh, OWNER_TG_USER_ID, must_exist=True)
        meeting = owner.meetups[0]

        # Property (a): the default load reaches the owned meeting's one-hop `joined_links`...
        assert "joined_links" in db.loaded_attributes(meeting)
        link = meeting.joined_links[0]
        # ...but the cycle stops the cascade before each link's participant leaves.
        before = db.loaded_attributes(link)
        assert "user" not in before
        assert "invited_by" not in before

        # Property (b): the meetup-rooted reload re-hydrates the leaves even though the meetup is
        # already identity-mapped with `joined_links` loaded, and returns the same instance.
        reloaded = await Meetup.by_id(fresh, meeting.db_id, for_update=for_update)
        assert reloaded is meeting
        link = reloaded.joined_links[0]
        after = db.loaded_attributes(link)
        assert "user" in after
        assert "invited_by" in after

        # The leaves are what the card renderer reads in plain Python.
        assert link.user.display_name == "Cycle Participant"
        assert link.invited_by is not None
        assert link.invited_by.inline_name == "Cycle Inviter"
