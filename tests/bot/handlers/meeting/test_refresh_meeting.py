import pytest
from telegram import Update

from mitup_bot.handlers.meeting.enums import MeetingHandlerId
from mitup_bot.models import User
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import meeting as meeting_views
from tests.helpers import (
    HandlerContext,
    MockDbSession,
    UpdateRequest,
    call_handler,
    create_joined_link,
    create_meetup,
    create_settings,
    create_user,
)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.REFRESH_MEETING.with_id(1))], indirect=True)
async def test_refresh_redraws_the_owner_card_with_the_current_participants(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)
    guest = create_user(id=42, tg_user_id=42, first_name="Guest")
    create_joined_link(user=guest, meetup=meeting, id=1)

    context, _ = await call_handler(MeetingHandlerId.REFRESH, handler_context=handler_context)

    context.api.assert_edit_message_called(update, meeting_views.owner_view(meeting))
    context.api.assert_send_message_not_called()
    assert "Guest" in meeting_views.owner_view(meeting).message.text


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.REFRESH_MEETING.with_id(7))], indirect=True)
async def test_refresh_redraws_the_participant_card_for_a_participant(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    owner = create_user(id=999, tg_user_id=9990, first_name="Owner", settings=create_settings(id=2))
    joined_meeting = create_meetup(id=7, owner=owner, title="Owner's Meeting")
    user_with_settings.joined_links = [create_joined_link(user=user_with_settings, meetup=joined_meeting)]
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(joined_meeting)

    context, _ = await call_handler(MeetingHandlerId.REFRESH, handler_context=handler_context)

    context.api.assert_edit_message_called(update, meeting_views.external_view(joined_meeting))


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.REFRESH_MEETING.with_id(1))], indirect=True)
async def test_refresh_does_not_store_or_update_any_meeting_message(
    mock_session: MockDbSession,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    meeting = user_with_settings.meetups[0]
    mock_session.add_object(meeting)

    context, _ = await call_handler(MeetingHandlerId.REFRESH, handler_context=handler_context)

    assert meeting.messages == []
    context.api.assert_update_meeting_messages_not_called()
