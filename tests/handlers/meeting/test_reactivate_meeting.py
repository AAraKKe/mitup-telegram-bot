import datetime as dt
from unittest import mock

import pytest
from telegram import Update

from mitup_bot import supporter
from mitup_bot.config import LimitsConfig
from mitup_bot.handlers.meeting.enums import MeetingHandlerId
from mitup_bot.models import Meetup, User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import MeetingLifecycleMessages, SupporterMessages
from tests.helpers import (
    HandlerContext,
    MockDbSession,
    UpdateRequest,
    call_handler,
)

MEETING_ID = 1


@pytest.fixture
def inactive_meeting(user_with_settings: User):
    meeting = user_with_settings.meetups[0]
    meeting.active = False
    # Use a non-None sentinel so the test can verify the handler clears this field
    meeting.expiration_time = dt.datetime(2099, 1, 1, tzinfo=dt.UTC)
    meeting.expiration_notification_sent = True
    return meeting


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.REACTIVATE_MEETING.with_id(MEETING_ID))], indirect=True
)
async def test_reactivate_meeting_sets_active_and_shows_edit_view(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    inactive_meeting: Meetup,
    handler_context: HandlerContext,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(inactive_meeting)

    context, _ = await call_handler(MeetingHandlerId.REACTIVATE_MEETING_CALLBACK, handler_context=handler_context)

    assert inactive_meeting.active is True
    assert inactive_meeting.expiration_time is None
    assert inactive_meeting.expiration_notification_sent is False

    success_message = MeetingLifecycleMessages.REACTIVATE_SUCCESS.get(lang=user_with_settings.lang)
    context.api.assert_edit_message_called(
        update,
        inactive_meeting.edit_view.with_context(success_message),
    )


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.REACTIVATE_MEETING.with_id(MEETING_ID))], indirect=True
)
async def test_reactivate_meeting_returns_silently_when_full_meeting_not_found(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """When await Meetup.by_id(include_inactive=True) returns None, the handler returns silently
    without editing any message."""
    mock_session.add_object(user_with_settings, "tg_user_id")
    # Add the meeting so user_owns_meeting succeeds, but patch by_id to return None
    # for the full meeting lookup (include_inactive=True).
    mock_session.add_object(user_with_settings.meetups[0])

    with mock.patch.object(Meetup, "by_id", return_value=None):
        context, _ = await call_handler(MeetingHandlerId.REACTIVATE_MEETING_CALLBACK, handler_context=handler_context)

    # Handler returned silently — no edit_message call
    context.api.assert_method_just_called("edit_message", times=0)


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
    context.api.assert_answer_callback_query_called(
        update=update,
        text=SupporterMessages.ACTIVE_MEETINGS_CAP.get_text(lang=user_with_settings.lang, cap=1),
        show_alert=True,
    )
    context.api.assert_method_just_called("edit_message", times=0)


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
