"""DB-integration tests for the per-owner lifecycle windows the events jobs query.

Every window in `mitup_bot.lifecycle` reaches production as an interval comparison joined to the
owner's `supporter_level`: the dateless deactivation window in `inactive_meetings`, and the
deletion-warning and permanent-deletion windows in `meetups_cleanup`. Which rows each one selects is
decided by real SQL over real rows — a mock session cannot evaluate the join or the interval
arithmetic — so the cases live here. The day offsets are derived from the policy, so the tests follow
a retuned duration instead of pinning a stale number (the values themselves are pinned in
`tests/core/test_lifecycle.py`).

Throwaway data uses the 998_95x-998_97x range (single-session, never committed); the lower 998_9xx
ids belong to `test_meeting_deactivation_cleanup.py`.
"""

import datetime as dt

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel.sql.expression import SelectOfScalar

from mitup_bot.events.inactive_meetings import MEETINGS_TO_DEACTIVATE_STATEMENT
from mitup_bot.events.meetups_cleanup import MEETUPS_ABOUT_TO_BE_DELETED_STATEMENT, MEETUPS_TO_DELETE_STATEMENT
from mitup_bot.lifecycle import LifecyclePolicy
from mitup_bot.models import Meetup
from mitup_bot.models.users import UserStatus
from mitup_bot.supporter import SupporterLevel
from tests.helpers import create_meetup, create_settings, create_user

pytestmark = pytest.mark.db_test

FREE_LEVEL = SupporterLevel.NONE
PATRON_LEVEL = SupporterLevel.HOST_2
ORGANIZER_LEVEL = SupporterLevel.HOST_3


def days_ago(days: int) -> dt.datetime:
    """A naive UTC timestamp `days` in the past, matching how the timestamp columns are stored."""
    return dt.datetime.now(dt.UTC).replace(tzinfo=None) - dt.timedelta(days=days)


def dateless_days(level: SupporterLevel) -> int:
    return LifecyclePolicy.interval_days(LifecyclePolicy.get(level).dateless_lifetime)


def retention_days(level: SupporterLevel) -> int:
    return LifecyclePolicy.interval_days(LifecyclePolicy.get(level).inactive_retention)


def warning_days(level: SupporterLevel) -> int:
    return LifecyclePolicy.interval_days(LifecyclePolicy.get(level).deletion_warning_delay)


def lead_days(level: SupporterLevel) -> int:
    return LifecyclePolicy.interval_days(LifecyclePolicy.get(level).deletion_warning_lead)


async def owned_meeting(
    db_session: AsyncSession,
    ref: int,
    *,
    level: SupporterLevel = FREE_LEVEL,
    status: UserStatus = UserStatus.MEMBER,
    activated_days_ago: int = 0,
    created_days_ago: int | None = None,
    active: bool = True,
    expiration_days_ago: int | None = None,
    notification_sent: bool = False,
    warned_days_ago: int | None = None,
) -> Meetup:
    """Persist a meeting under an owner of its own and return it.

    `ref` seeds the owner's `tg_user_id` and the explicit ids of all three rows, so every call lands
    a scenario that cannot collide with another test's inside the shared session transaction.

    `warned_days_ago` defaults to the age of the expiration when the notification flag is set, which
    is the shape the sweep leaves behind on a run that warned the meeting the day it came due. A case
    that needs the two to disagree — the whole point of the lead gate — passes it explicitly.
    """
    warned_age = expiration_days_ago if notification_sent and warned_days_ago is None else warned_days_ago
    meeting = create_meetup(
        ref,
        title="Lifecycle Window Meeting",
        created_time=days_ago(activated_days_ago if created_days_ago is None else created_days_ago),
        activated_time=days_ago(activated_days_ago),
        expiration_time=None if expiration_days_ago is None else days_ago(expiration_days_ago),
        expiration_notification_sent=notification_sent,
        warned_time=None if warned_age is None else days_ago(warned_age),
        active=active,
    )
    owner = create_user(
        id=ref,
        tg_user_id=ref,
        first_name=f"Lifecycle {ref}",
        status=status,
        supporter_level=level,
        settings=create_settings(id=ref),
        owned_meetings=[meeting],
    )
    db_session.add(owner)
    await db_session.flush()
    return meeting


async def selected_ids(db_session: AsyncSession, statement: SelectOfScalar[Meetup]) -> set[int | None]:
    return {meeting.id for meeting in (await db_session.exec(statement)).all()}


async def is_due_for_deactivation(db_session: AsyncSession, meeting: Meetup) -> bool:
    return meeting.id in await selected_ids(db_session, MEETINGS_TO_DEACTIVATE_STATEMENT)


async def is_due_for_warning(db_session: AsyncSession, meeting: Meetup) -> bool:
    return meeting.id in await selected_ids(db_session, MEETUPS_ABOUT_TO_BE_DELETED_STATEMENT)


async def is_due_for_deletion(db_session: AsyncSession, meeting: Meetup) -> bool:
    return meeting.id in await selected_ids(db_session, MEETUPS_TO_DELETE_STATEMENT)


# --- Dateless deactivation: the window depends on the owner's tier ---


async def test_free_owner_dateless_meeting_past_its_window_is_due(db_session: AsyncSession):
    meeting = await owned_meeting(db_session, 998_950, activated_days_ago=dateless_days(FREE_LEVEL) + 10)

    assert await is_due_for_deactivation(db_session, meeting) is True


async def test_free_owner_dateless_meeting_inside_its_window_is_not_due(db_session: AsyncSession):
    meeting = await owned_meeting(db_session, 998_951, activated_days_ago=dateless_days(FREE_LEVEL) - 10)

    assert await is_due_for_deactivation(db_session, meeting) is False


@pytest.mark.parametrize(("level", "ref"), [(PATRON_LEVEL, 998_952), (ORGANIZER_LEVEL, 998_953)])
async def test_paying_owner_keeps_a_dateless_meeting_past_the_free_window(
    db_session: AsyncSession, level: SupporterLevel, ref: int
):
    """The same age that deactivates a free host's draft leaves a supporter's draft alive: the
    window is read from the owner's own tier, not from a single global interval."""
    meeting = await owned_meeting(db_session, ref, level=level, activated_days_ago=dateless_days(FREE_LEVEL) + 10)

    assert await is_due_for_deactivation(db_session, meeting) is False


@pytest.mark.parametrize(("level", "ref"), [(PATRON_LEVEL, 998_954), (ORGANIZER_LEVEL, 998_955)])
async def test_paying_owner_dateless_meeting_past_its_own_window_is_due(
    db_session: AsyncSession, level: SupporterLevel, ref: int
):
    """A draft ends for every tier, the Organizer included — the unlimited scheduling horizon does
    not make an undated meeting immortal."""
    meeting = await owned_meeting(db_session, ref, level=level, activated_days_ago=dateless_days(level) + 10)

    assert await is_due_for_deactivation(db_session, meeting) is True


async def test_left_owner_shortens_the_window_even_for_a_supporter(db_session: AsyncSession):
    """An owner who left the bot keeps no drafts alive: the LEFT window applies on top of the tier
    window, so a Patron's draft that is past it is due well before the Patron window ends."""
    left_days = LifecyclePolicy.interval_days(LifecyclePolicy.get().left_owner_dateless_lifetime)
    meeting = await owned_meeting(
        db_session, 998_956, level=PATRON_LEVEL, status=UserStatus.LEFT, activated_days_ago=left_days + 5
    )

    assert left_days + 5 < dateless_days(PATRON_LEVEL)
    assert await is_due_for_deactivation(db_session, meeting) is True


async def test_left_owner_dateless_meeting_inside_the_left_window_is_not_due(db_session: AsyncSession):
    meeting = await owned_meeting(
        db_session,
        998_957,
        status=UserStatus.LEFT,
        activated_days_ago=LifecyclePolicy.interval_days(LifecyclePolicy.get().left_owner_dateless_lifetime) - 5,
    )

    assert await is_due_for_deactivation(db_session, meeting) is False


async def test_left_window_applies_only_to_a_left_owner(db_session: AsyncSession):
    """The short window is gated on the owner's status: a draft that old under an active owner still
    runs the full tier window."""
    left_days = LifecyclePolicy.interval_days(LifecyclePolicy.get().left_owner_dateless_lifetime)
    meeting = await owned_meeting(db_session, 998_972, status=UserStatus.MEMBER, activated_days_ago=left_days + 10)

    assert left_days + 10 < dateless_days(FREE_LEVEL)
    assert await is_due_for_deactivation(db_session, meeting) is False


@pytest.mark.parametrize(
    ("day_offset", "due", "ref"), [(-1, False, 998_973), (1, True, 998_974)], ids=["day_before", "day_after"]
)
async def test_dateless_window_turns_over_on_its_own_day(
    db_session: AsyncSession, day_offset: int, due: bool, ref: int
):
    """Both sides of the boundary: a day short of the window the draft stays active — the owner may
    still date it — and a day past it the sweep takes it."""
    meeting = await owned_meeting(db_session, ref, activated_days_ago=dateless_days(FREE_LEVEL) + day_offset)

    assert await is_due_for_deactivation(db_session, meeting) is due


async def test_reactivated_meeting_is_not_deactivated_again_on_its_old_age(db_session: AsyncSession):
    """A reactivation restarts the meeting: the sweep reads `activated_time`, so a long-dated
    `created_time` does not drag the draft straight back out of the active state."""
    meeting = await owned_meeting(
        db_session, 998_958, activated_days_ago=0, created_days_ago=dateless_days(FREE_LEVEL) + 100
    )

    assert await is_due_for_deactivation(db_session, meeting) is False


# --- Inactive retention: deletion warning and permanent deletion ---


@pytest.mark.parametrize(("level", "ref"), [(FREE_LEVEL, 998_960), (PATRON_LEVEL, 998_961)])
async def test_inactive_meeting_past_its_tier_retention_is_deleted(
    db_session: AsyncSession, level: SupporterLevel, ref: int
):
    meeting = await owned_meeting(
        db_session,
        ref,
        level=level,
        active=False,
        expiration_days_ago=retention_days(level) + 5,
        notification_sent=True,
    )

    assert await is_due_for_deletion(db_session, meeting) is True


async def test_patron_inactive_meeting_survives_the_free_retention(db_session: AsyncSession):
    """The retention a free host's meeting is deleted at leaves a supporter's meeting in place."""
    meeting = await owned_meeting(
        db_session,
        998_962,
        level=PATRON_LEVEL,
        active=False,
        expiration_days_ago=retention_days(FREE_LEVEL) + 5,
        notification_sent=True,
    )

    assert await is_due_for_deletion(db_session, meeting) is False


@pytest.mark.parametrize(("level", "ref"), [(FREE_LEVEL, 998_963), (PATRON_LEVEL, 998_964)])
async def test_inactive_meeting_inside_its_tier_retention_is_kept(
    db_session: AsyncSession, level: SupporterLevel, ref: int
):
    meeting = await owned_meeting(
        db_session,
        ref,
        level=level,
        active=False,
        expiration_days_ago=retention_days(level) - 20,
        notification_sent=True,
    )

    assert await is_due_for_deletion(db_session, meeting) is False


async def test_unwarned_meeting_past_its_retention_is_not_deleted(db_session: AsyncSession):
    """The owner gets the warning first: a meeting past its retention that was never notified is
    picked up by the warning query, not by the deletion query."""
    meeting = await owned_meeting(
        db_session,
        998_965,
        active=False,
        expiration_days_ago=retention_days(FREE_LEVEL) + 5,
        notification_sent=False,
    )

    assert await is_due_for_deletion(db_session, meeting) is False
    assert await is_due_for_warning(db_session, meeting) is True


@pytest.mark.parametrize(("level", "ref"), [(FREE_LEVEL, 998_966), (PATRON_LEVEL, 998_967)])
async def test_warning_fires_inside_the_lead_before_the_deletion(
    db_session: AsyncSession, level: SupporterLevel, ref: int
):
    """Aged to one day past the warning point, the meeting is warned but not yet deletable — the
    owner has the lead time to reactivate it."""
    meeting = await owned_meeting(
        db_session, ref, level=level, active=False, expiration_days_ago=warning_days(level) + 1
    )

    assert await is_due_for_warning(db_session, meeting) is True

    meeting.record_deletion_warning()
    await db_session.flush()
    assert await is_due_for_deletion(db_session, meeting) is False


@pytest.mark.parametrize(("level", "ref"), [(FREE_LEVEL, 998_968), (PATRON_LEVEL, 998_969)])
async def test_no_warning_before_the_lead_starts(db_session: AsyncSession, level: SupporterLevel, ref: int):
    meeting = await owned_meeting(
        db_session, ref, level=level, active=False, expiration_days_ago=warning_days(level) - 1
    )

    assert await is_due_for_warning(db_session, meeting) is False


async def test_warned_meeting_is_deleted_once_the_retention_runs_out(db_session: AsyncSession):
    """The two windows meet: the same meeting that was warned earlier is deleted once its full
    retention has elapsed."""
    meeting = await owned_meeting(
        db_session,
        998_970,
        active=False,
        expiration_days_ago=retention_days(FREE_LEVEL) + 1,
        notification_sent=True,
    )

    assert await is_due_for_deletion(db_session, meeting) is True


# --- The lead the warning promised is owed from when it was sent, not from the deactivation ---


async def test_a_late_warning_carries_the_deletion_with_it(db_session: AsyncSession):
    """The warning tells the owner their meeting goes in `deletion_warning_lead` days. A sweep that
    fell behind can issue it long after the retention already ran out, and the owner still gets the
    days they were promised — counted from the warning, not from the deactivation that preceded it."""
    meeting = await owned_meeting(
        db_session,
        998_971,
        active=False,
        expiration_days_ago=retention_days(FREE_LEVEL) + 40,
        notification_sent=True,
        warned_days_ago=lead_days(FREE_LEVEL) - 1,
    )

    assert await is_due_for_deletion(db_session, meeting) is False


async def test_the_deletion_follows_once_the_promised_lead_has_run(db_session: AsyncSession):
    """The other side of the same boundary: once the lead the warning named has passed, the retention
    gate is already long open and the meeting goes."""
    meeting = await owned_meeting(
        db_session,
        998_975,
        active=False,
        expiration_days_ago=retention_days(FREE_LEVEL) + 40,
        notification_sent=True,
        warned_days_ago=lead_days(FREE_LEVEL) + 1,
    )

    assert await is_due_for_deletion(db_session, meeting) is True


async def test_a_row_flagged_as_warned_without_a_stamp_is_never_deleted(db_session: AsyncSession):
    """The shape a row would have if it were flagged as warned with no record of when. NULL fails the
    interval comparison rather than passing it, so the missing stamp cannot be read as an ancient one
    and the row is left for an operator instead of deleted on no evidence."""
    meeting = await owned_meeting(
        db_session,
        998_976,
        active=False,
        expiration_days_ago=retention_days(FREE_LEVEL) + 40,
        notification_sent=True,
    )
    meeting.warned_time = None
    await db_session.flush()

    assert await is_due_for_deletion(db_session, meeting) is False
