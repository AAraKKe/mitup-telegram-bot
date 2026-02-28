import pytest
from telegram import Update

from mitup_bot.handlers.meeting.enums import MeetingHandlerId
from mitup_bot.models import User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingMessages
from mitup_bot.views import ButtonConfig, MitupView, factory
from tests.helpers import (
    MockDbSession,
    StubMitupApp,
    UpdateRequest,
    call_handler,
)

MEETING_ID = 1


@pytest.fixture
def inactive_meeting(user_with_settings: User):
    meeting = user_with_settings.meetups[0]
    meeting.active = False
    return meeting


def _expected_past_meeting_view(meeting, user: User) -> MitupView:
    description = MeetingMessages.PAST_MEETING_DESCRIPTION.get(lang=user.lang)
    return MitupView(
        meeting.message,
        [
            [
                ButtonConfig(
                    text=ButtonMessages.REACTIVATE_MEETING.get(lang=user.lang),
                    callback_data=cb.REACTIVATE_MEETING.with_id(meeting.db_id),
                ),
                ButtonConfig(
                    text=ButtonMessages.DELETE.get(lang=user.lang),
                    callback_data=cb.DELETE_PAST_MEETING.with_id(meeting.db_id),
                ),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.PAST_MEETINGS.back(lang=user.lang),
                    callback_data=cb.SHOW_PAST_MEETING_PAGE.with_id(1),
                ),
            ],
        ],
    ).with_context(description)


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.DELETE_PAST_MEETING.with_id(MEETING_ID))], indirect=True
)
async def test_delete_past_meeting_shows_confirmation(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    inactive_meeting,
    app: StubMitupApp,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(inactive_meeting)

    context, _ = await call_handler(MeetingHandlerId.DELETE_PAST_MEETING_CALLBACK, update=update, app=app)

    mock_session.assert_not_deleted()
    context.api.assert_send_message_called(
        update,
        factory.confirmation_view(
            lang=user_with_settings.lang,
            message=MeetingMessages.DELETE_MEETING.get(lang=user_with_settings.lang),
            confirm_callback_data=cb.CONFIRM_DELETE_PAST_MEETING.with_id(MEETING_ID),
            decline_callback_data=cb.DECLINE_DELETE_PAST_MEETING.with_id(MEETING_ID),
        ),
    )


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.SHOW_PAST_MEETING.with_id(MEETING_ID))], indirect=True
)
async def test_show_past_meeting_renders_detail_view(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    inactive_meeting,
    app: StubMitupApp,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(inactive_meeting)

    context, _ = await call_handler(MeetingHandlerId.SHOW_PAST_MEETING_CALLBACK, update=update, app=app)

    context.api.assert_edit_message_called(update, _expected_past_meeting_view(inactive_meeting, user_with_settings))


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.CONFIRM_DELETE_PAST_MEETING.with_id(MEETING_ID))], indirect=True
)
async def test_confirm_delete_past_meeting_deletes_and_redirects_to_past_meetings(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    inactive_meeting,
    app: StubMitupApp,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(inactive_meeting)

    context, _ = await call_handler(MeetingHandlerId.CONFIRM_DELETE_PAST_MEETING_CALLBACK, update=update, app=app)

    mock_session.assert_deleted(inactive_meeting)
    context.api.assert_update_meeting_messages_called(session=mock_session, meeting=inactive_meeting, was_deleted=True)
    context.api.assert_send_message_called(
        update,
        MitupView(
            description=MeetingMessages.DELETE_MEETING_SUCCESS.get(lang=user_with_settings.lang),
            keyboard=[
                [
                    ButtonConfig(
                        text=ButtonMessages.PAST_MEETINGS.back(lang=user_with_settings.lang),
                        callback_data=cb.SHOW_PAST_MEETING_PAGE.with_id(1),
                    )
                ]
            ],
        ),
    )


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.DECLINE_DELETE_PAST_MEETING.with_id(MEETING_ID))], indirect=True
)
async def test_decline_delete_past_meeting_returns_to_past_meeting_view(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    inactive_meeting,
    app: StubMitupApp,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(inactive_meeting)

    context, _ = await call_handler(MeetingHandlerId.DECLINE_DELETE_PAST_MEETING_CALLBACK, update=update, app=app)

    mock_session.assert_not_deleted()
    context.api.assert_edit_message_called(update, _expected_past_meeting_view(inactive_meeting, user_with_settings))
