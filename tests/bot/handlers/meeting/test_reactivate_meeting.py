import datetime as dt

import pytest
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs
from telegram import Update

from mitup_bot import supporter
from mitup_bot.config import LimitsConfig
from mitup_bot.handlers.meeting.enums import MeetingHandlerId
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Meetup, User
from mitup_bot.monitoring import Feature, MetricKey
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingLifecycleMessages, SupporterMessages
from mitup_bot.views import RenderContext, factory
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.collaborate import supporter_upsell_view
from tests.helpers import (
    HandlerContext,
    MetricAssertions,
    MockDbSession,
    UpdateRequest,
    call_handler,
)

MEETING_ID = 1


@pytest.fixture
def inactive_meeting(user_with_settings: User):
    meeting = user_with_settings.meetups[0]
    meeting.active = False
    # Non-None sentinels so the test can verify the handler clears each of these fields
    meeting.expiration_time = dt.datetime(2099, 1, 1, tzinfo=dt.UTC)
    meeting.expiration_notification_sent = True
    meeting.warned_time = dt.datetime(2099, 1, 2, tzinfo=dt.UTC)
    meeting.datetime = dt.datetime(2020, 3, 1, 18, 0, tzinfo=dt.UTC)
    meeting.end_datetime = dt.datetime(2020, 3, 1, 20, 0, tzinfo=dt.UTC)
    meeting.lock_on_start = True
    meeting.activated_time = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
    return meeting


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.REACTIVATE_MEETING.with_id(MEETING_ID))], indirect=True
)
async def test_reactivate_meeting_restarts_the_meeting_and_shows_edit_view(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    inactive_meeting: Meetup,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """Reactivation is a restart: the meeting comes back active as a dateless draft with a fresh
    activation stamp, so it gets its full dateless window instead of the one it already spent."""
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(inactive_meeting)
    before = dt.datetime.now(dt.UTC)

    context, _ = await call_handler(MeetingHandlerId.REACTIVATE_MEETING_CALLBACK, handler_context=handler_context)

    assert inactive_meeting.active is True
    assert inactive_meeting.expiration_time is None
    # The flag and the stamp are the deletion sweep's two gates; a reactivation that dropped only one
    # would leave a meeting warned in the eyes of whichever gate it forgot.
    assert inactive_meeting.expiration_notification_sent is False
    assert inactive_meeting.warned_time is None
    assert inactive_meeting.datetime is None
    assert inactive_meeting.end_datetime is None
    assert inactive_meeting.lock_on_start is False
    assert before <= inactive_meeting.activated_time <= dt.datetime.now(dt.UTC)

    success_message = MeetingLifecycleMessages.REACTIVATE_SUCCESS.get(lang=user_with_settings.lang)
    context.api.assert_edit_message_called(
        update,
        meeting_views.edit_view(inactive_meeting).with_context(success_message),
    )
    metrics.assert_emitted(name=MetricKey.COUNT, value=1, dimensions={"Feature": str(Feature.REACTIVATE_MEETING)})


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.REACTIVATE_MEETING.with_id(MEETING_ID))], indirect=True
)
async def test_reactivate_meeting_leaves_an_already_active_meeting_untouched(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    inactive_meeting: Meetup,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """A second tap on a Reactivate button the owner already used must not restart a live meeting:
    the dates they set after the first tap survive, and the owner just lands on the edit screen."""
    inactive_meeting.active = True
    scheduled_start = dt.datetime(2030, 5, 1, 17, 0, tzinfo=dt.UTC)
    inactive_meeting.datetime = scheduled_start
    activated = inactive_meeting.activated_time
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(inactive_meeting)

    context, _ = await call_handler(MeetingHandlerId.REACTIVATE_MEETING_CALLBACK, handler_context=handler_context)

    assert inactive_meeting.datetime == scheduled_start
    assert inactive_meeting.activated_time == activated
    context.api.assert_edit_message_called(update, meeting_views.edit_view(inactive_meeting))
    metrics.assert_not_emitted(name=MetricKey.COUNT, dimensions={"Feature": str(Feature.REACTIVATE_MEETING)})


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.REACTIVATE_MEETING.with_id(MEETING_ID))], indirect=True
)
async def test_reactivate_meeting_redirects_when_meeting_no_longer_exists(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """A meeting that no longer resolves is answered like one the user does not own."""
    # Register the user but not the meeting, so the guard's meeting-rooted load finds nothing.
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(MeetingHandlerId.REACTIVATE_MEETING_CALLBACK, handler_context=handler_context)

    context.api.assert_edit_message_called(update, factory.main_menu_view(RenderContext(lang=user_with_settings.lang)))


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.REACTIVATE_MEETING.with_id(MEETING_ID))], indirect=True
)
async def test_reactivate_meeting_blocked_when_at_active_meetings_cap(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    inactive_meeting: Meetup,
    handler_context: HandlerContext,
    monkeypatch: pytest.MonkeyPatch,
):
    """A free user already at their cap cannot reactivate: the meeting stays inactive and the upsell
    alert is shown. The other meeting the fixture owns stays active — the cap counts it."""
    # Cap of 1; the fixture's other meeting (id 2) is active, so the user is already at the cap.
    monkeypatch.setattr(supporter.PolicyState, "config", LimitsConfig(free_active_meetings=1))
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(inactive_meeting)

    context, _ = await call_handler(MeetingHandlerId.REACTIVATE_MEETING_CALLBACK, handler_context=handler_context)

    assert inactive_meeting.active is False  # not reactivated
    context.api.assert_method_just_called("answer_callback_query", times=0)
    past_meetings_button = ButtonConfig(
        text=ButtonMessages.PAST_MEETINGS.back(lang=user_with_settings.lang),
        callback_data=cb.SHOW_PAST_MEETING_PAGE.with_id(1),
    )
    context.api.assert_edit_message_called(
        update,
        supporter_upsell_view(
            SupporterMessages.ACTIVE_MEETINGS_CAP.get(lang=user_with_settings.lang, cap=1),
            user_with_settings.lang,
        ).with_context_menu([[past_meetings_button]]),
    )


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.REACTIVATE_MEETING.with_id(MEETING_ID))], indirect=True
)
async def test_reactivate_meeting_allowed_below_cap(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    inactive_meeting: Meetup,
    handler_context: HandlerContext,
    monkeypatch: pytest.MonkeyPatch,
):
    """With headroom under the cap the reactivation goes through as normal."""
    monkeypatch.setattr(supporter.PolicyState, "config", LimitsConfig(free_active_meetings=5))
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(inactive_meeting)

    context, _ = await call_handler(MeetingHandlerId.REACTIVATE_MEETING_CALLBACK, handler_context=handler_context)

    assert inactive_meeting.active is True
    context.api.assert_method_just_called("answer_callback_query", times=0)


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.REACTIVATE_MEETING.with_id(MEETING_ID))], indirect=True
)
async def test_reactivation_records_the_pending_deletion_it_cancelled(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    inactive_meeting: Meetup,
    handler_context: HandlerContext,
):
    """The writes reset the retention clock, discard the record that the owner was warned, and
    restart the dateless window. Recorded before the mutation, which is the last moment the values
    they overwrite still exist."""
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(inactive_meeting)

    with capture_logs(processors=[merge_contextvars]) as logs:
        await call_handler(MeetingHandlerId.REACTIVATE_MEETING_CALLBACK, handler_context=handler_context)

    record = next(entry for entry in logs if entry["event"] == "Meeting reactivated")
    assert record["meeting_id"] == MEETING_ID
    assert record["tg_user_id"] == user_with_settings.tg_user_id
    assert record["previous_activated_time"] == dt.datetime(2020, 1, 1, tzinfo=dt.UTC)
    assert record["previous_expiration_time"] == dt.datetime(2099, 1, 1, tzinfo=dt.UTC)
    assert record["was_warned"] is True
    assert record["reason"] == "owner_reactivated"


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.REACTIVATE_MEETING.with_id(MEETING_ID))], indirect=True
)
async def test_a_second_tap_on_a_stale_warning_is_recorded_as_a_skip(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    inactive_meeting: Meetup,
    handler_context: HandlerContext,
):
    """A stale warning DM stays tappable, and the early return emits no metric — without a line of
    its own a second tap is indistinguishable from a real rescue by absence alone."""
    inactive_meeting.active = True
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(inactive_meeting)

    with capture_logs(processors=[merge_contextvars]) as logs:
        await call_handler(MeetingHandlerId.REACTIVATE_MEETING_CALLBACK, handler_context=handler_context)

    record = next(entry for entry in logs if entry["event"] == "Skip meeting reactivation")
    assert (record["meeting_id"], record["reason"]) == (MEETING_ID, "already_active")
    assert not [entry for entry in logs if entry["event"] == "Meeting reactivated"]
