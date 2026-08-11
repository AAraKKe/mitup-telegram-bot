"""The shared emitters of the meeting domain.

Each of these covers one mechanism that several call sites route through, so a regression in the
mechanism is caught once rather than being restated per handler. The per-handler lines that simply
use them are not re-asserted here.
"""

import datetime as dt

import pytest
from structlog.testing import capture_logs
from structlog.typing import EventDict
from telegram import Update

from mitup_bot.handlers.meeting.edit.enums import EditMeetingHandlerId
from mitup_bot.handlers.meeting.edit.when.rules import apply_end_datetime, apply_start_datetime
from mitup_bot.handlers.meeting.enums import MeetingHandlerId
from mitup_bot.handlers.meeting.utils import (
    active_meetings_cap_reached,
    log_waiting_list_promotions,
    main_menu_back_button,
    participant_capacity_rejection,
    scheduling_horizon_rejection,
)
from mitup_bot.models import User
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils import callbacks as cb
from tests.helpers import (
    HandlerContext,
    MockDbSession,
    StubMitupContext,
    UpdateRequest,
    call_handler,
    create_joined_link,
    create_meetup,
    create_user,
)
from tests.helpers.types import ClaimSharedCard


def lines(logs: list[EventDict], event: str) -> list[EventDict]:
    return [entry for entry in logs if entry["event"] == event]


def only(logs: list[EventDict], event: str) -> EventDict:
    matching = lines(logs, event)
    assert len(matching) == 1, f"expected one {event!r} line, captured {[entry['event'] for entry in logs]}"
    return matching[0]


# ---------------------------------------------------------------------------
# The join vocabulary: one event name, one outcome/reason pair per way a tap can end
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.JOIN.with_id(123), from_bot_chat=False)], indirect=True
)
@pytest.mark.parametrize(
    "max_members, waiting_list, already_joined, outcome, reason",
    [
        (None, False, False, "joined", "ok"),
        (1, True, False, "waiting_list", "meeting_full_waiting_list_enabled"),
        (1, False, False, "refused", "meeting_full_no_waiting_list"),
        (None, False, True, "already_joined", "already_member"),
    ],
    ids=["joined", "waiting_list", "refused_full", "already_member"],
)
async def test_every_way_a_join_can_end_is_named(
    user_with_settings: User,
    mock_session: MockDbSession,
    handler_context: HandlerContext,
    claim_shared_card: ClaimSharedCard,
    max_members: int | None,
    waiting_list: bool,
    already_joined: bool,
    outcome: str,
    reason: str,
):
    """ "Why couldn't I join?" has four different answers and the card shows an alert for all of them."""
    owner = create_user(id=99, first_name="Owner", tg_user_id=1)
    meeting = create_meetup(id=123, max_members=max_members, waiting_list=waiting_list, owner=owner)
    create_joined_link(user=owner, meetup=meeting)
    if already_joined:
        create_joined_link(user=user_with_settings, meetup=meeting)

    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(meeting)
    claim_shared_card(meeting)

    with capture_logs() as logs:
        await call_handler(MeetingHandlerId.JOIN, handler_context=handler_context)

    resolved = only(logs, "Meeting join resolved")
    assert (resolved["outcome"], resolved["reason"]) == (outcome, reason)
    assert resolved["joining_user_id"] == user_with_settings.db_id
    assert resolved["waiting_list_enabled"] is waiting_list


# ---------------------------------------------------------------------------
# The shared promotion event: same name from every trigger, told apart by reason
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reason", ["participant_left", "participant_kicked_out"])
def test_a_promotion_names_what_freed_the_spot(reason: str):
    meeting = create_meetup(id=1, max_members=2, waiting_list=True, owner=create_user(id=99))
    promoted = create_joined_link(user=create_user(id=7, tg_user_id=7), meetup=meeting)

    with capture_logs() as logs:
        log_waiting_list_promotions(meeting, [promoted], reason=reason)

    promotion = only(logs, "Waiting list promoted")
    assert (promotion["reason"], promotion["promoted_count"], promotion["promoted_user_ids"]) == (reason, 1, [7])


def test_promoting_nobody_is_not_an_event():
    """Every leave and kick-out calls this; only the ones that moved the queue may write a line."""
    meeting = create_meetup(id=1, owner=create_user(id=99))

    with capture_logs() as logs:
        log_waiting_list_promotions(meeting, [], reason="participant_left")

    assert lines(logs, "Waiting list promoted") == []


# ---------------------------------------------------------------------------
# The datetime writes: the side effects land on the record of their cause
# ---------------------------------------------------------------------------


def test_a_start_time_that_clears_the_end_says_so_on_the_same_line():
    """`enforce_datetime_ordering` drops the end time and the lock rule with it, and the owner's
    screen mentions neither, so cause and effect have to share one record."""
    owner = create_user(id=5)
    start = dt.datetime(2030, 6, 1, 10, tzinfo=dt.UTC)
    meeting = create_meetup(id=1, owner=owner, datetime=start)
    meeting.end_datetime = dt.datetime(2030, 6, 1, 12, tzinfo=dt.UTC)
    meeting.lock_on_start = True

    with capture_logs() as logs:
        cleared = apply_start_datetime(meeting, dt.datetime(2030, 6, 2, 9, tzinfo=dt.UTC), input_source="time_message")

    assert cleared is True
    written = only(logs, "Meeting start datetime set")
    assert written["end_datetime_cleared"] is True
    assert written["lock_on_start_reset"] is True
    assert written["previous_end_datetime"] == dt.datetime(2030, 6, 1, 12, tzinfo=dt.UTC)
    assert (written["old_datetime"], written["input_source"], written["user_id"]) == (start, "time_message", 5)


def test_a_start_time_that_leaves_the_end_alone_claims_nothing():
    meeting = create_meetup(id=1, owner=create_user(id=5), datetime=dt.datetime(2030, 6, 1, 10, tzinfo=dt.UTC))
    meeting.end_datetime = dt.datetime(2030, 6, 5, 12, tzinfo=dt.UTC)
    meeting.lock_on_start = True

    with capture_logs() as logs:
        cleared = apply_start_datetime(
            meeting, dt.datetime(2030, 6, 2, 9, tzinfo=dt.UTC), input_source="calendar_update"
        )

    assert cleared is False
    written = only(logs, "Meeting start datetime set")
    assert (written["end_datetime_cleared"], written["lock_on_start_reset"]) == (False, False)


def test_an_end_time_reports_the_span_it_produces():
    meeting = create_meetup(id=1, owner=create_user(id=5), datetime=dt.datetime(2030, 6, 1, 10, tzinfo=dt.UTC))

    with capture_logs() as logs:
        apply_end_datetime(meeting, dt.datetime(2030, 6, 1, 11, 30, tzinfo=dt.UTC), input_source="datetime_entity")

    written = only(logs, "Meeting end datetime set")
    assert (written["duration_minutes"], written["old_end_datetime"], written["input_source"]) == (
        90,
        None,
        "datetime_entity",
    )


# ---------------------------------------------------------------------------
# The plan-cap refusals: three helpers, three events, one call site each in the log
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.CREATE_MEETING)], indirect=True)
async def test_the_active_meetings_cap_refusal_names_the_tier_it_applied(
    update: Update, context: StubMitupContext, user_with_settings: User
):
    """One helper is the sole cause of three silent conversation ends, and `trigger` is what tells
    the button paths apart from the inline deep-link one that only sends a message."""
    user_with_settings.meetups = [create_meetup(id=index, owner=user_with_settings) for index in range(1, 21)]

    with capture_logs() as logs:
        assert await active_meetings_cap_reached(
            user_with_settings, update, context, back_button=main_menu_back_button(user_with_settings.lang)
        )

    refusal = only(logs, "Meeting action refused by active meetings cap")
    assert refusal["log_level"] == "warning"
    assert (refusal["reason"], refusal["supporter_level"], refusal["trigger"]) == (
        "active_meetings_cap",
        SupporterLevel.NONE.value,
        "callback",
    )
    assert isinstance(refusal["cap"], int)


def test_the_scheduling_horizon_refusal_names_which_datetime_was_refused(user_with_settings: User):
    far_future = dt.datetime.now(dt.UTC) + dt.timedelta(days=3650)

    with capture_logs() as logs:
        assert scheduling_horizon_rejection(user_with_settings, far_future, field="end") is not None

    refusal = only(logs, "Meeting datetime refused by scheduling horizon")
    assert (refusal["reason"], refusal["field"], refusal["requested_datetime"]) == (
        "beyond_scheduling_horizon",
        "end",
        far_future,
    )


def test_the_participant_capacity_refusal_records_what_was_asked_for(user_with_settings: User):
    with capture_logs() as logs:
        assert participant_capacity_rejection(user_with_settings, 10_000) is not None

    refusal = only(logs, "Participant limit refused by plan cap")
    assert (refusal["reason"], refusal["requested_max"]) == ("participant_capacity_cap", 10_000)


def test_a_limit_within_the_plan_is_not_a_refusal(user_with_settings: User):
    """A warning must never be the only record of a normal outcome, so the allow path is silent here
    and the change itself is recorded by `Meeting capacity changed`."""
    with capture_logs() as logs:
        assert participant_capacity_rejection(user_with_settings, 2) is None

    assert lines(logs, "Participant limit refused by plan cap") == []


# ---------------------------------------------------------------------------
# The settings factory: one line instruments every boolean it generates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.SET_MEETING_PUBLIC.with_id(1))], indirect=True)
async def test_a_generated_toggle_names_the_attribute_it_flipped(
    user_with_settings: User, mock_session: MockDbSession, handler_context: HandlerContext
):
    """The `field` facet is the attribute name the factory was given, so a new toggle is recorded
    without anyone remembering to add a line for it."""
    meeting = user_with_settings.meetups[0]
    meeting.public = False

    mock_session.add_object(user_with_settings, query_field="tg_user_id")
    mock_session.add_object(meeting, query_field="id")

    with capture_logs() as logs:
        await call_handler(EditMeetingHandlerId.SET_MEETING_PUBLIC_CALLBACK, handler_context=handler_context)

    toggled = only(logs, "Meeting setting toggled")
    assert (toggled["field"], toggled["old_value"], toggled["new_value"], toggled["reason"]) == (
        "public",
        False,
        True,
        "owner_toggled",
    )
