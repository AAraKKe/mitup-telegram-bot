import pytest
from telegram import Update

from mitup_bot.handlers.meeting.enums import MeetingHandlerId
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Meetup, User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingLifecycleMessages
from mitup_bot.views import MitupView, RenderContext, factory
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
    return meeting


def expected_past_meeting_view(meeting: Meetup, user: User, page: int = 1) -> MitupView:
    description = MeetingLifecycleMessages.PAST_DESCRIPTION.get(lang=user.lang)
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
                    callback_data=cb.DELETE_PAST_MEETING.with_page(meeting.db_id, page),
                ),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.PAST_MEETINGS.back(lang=user.lang),
                    callback_data=cb.SHOW_PAST_MEETING_PAGE.with_id(page),
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
    inactive_meeting: Meetup,
    handler_context: HandlerContext,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(inactive_meeting)

    context, _ = await call_handler(MeetingHandlerId.DELETE_PAST_MEETING_CALLBACK, handler_context=handler_context)

    mock_session.assert_not_deleted()
    context.api.assert_edit_message_called(
        update,
        factory.confirmation_view(
            RenderContext(lang=user_with_settings.lang),
            message=MeetingLifecycleMessages.DELETE_CONFIRMATION.get(lang=user_with_settings.lang),
            confirm_callback_data=cb.CONFIRM_DELETE_PAST_MEETING.with_id(MEETING_ID),
            decline_callback_data=cb.DECLINE_DELETE_PAST_MEETING.with_id(MEETING_ID),
        ),
    )
    # Regression (issue #170): the confirmation must edit the detail view in place, never post a
    # new message that would leave the original message with live "Reactivate"/"Delete" buttons.
    context.api.assert_send_message_not_called()


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.SHOW_PAST_MEETING.with_id(MEETING_ID))], indirect=True
)
async def test_show_past_meeting_renders_detail_view(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    inactive_meeting: Meetup,
    handler_context: HandlerContext,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(inactive_meeting)

    context, _ = await call_handler(MeetingHandlerId.SHOW_PAST_MEETING_CALLBACK, handler_context=handler_context)

    context.api.assert_edit_message_called(update, expected_past_meeting_view(inactive_meeting, user_with_settings))


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.SHOW_PAST_MEETING.with_page(MEETING_ID, 3))], indirect=True
)
async def test_show_past_meeting_back_button_returns_to_originating_page(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    inactive_meeting: Meetup,
    handler_context: HandlerContext,
):
    """Opening a past meeting from page 3 must render a detail view whose Back button
    returns to page 3, not page 1."""
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(inactive_meeting)

    context, _ = await call_handler(MeetingHandlerId.SHOW_PAST_MEETING_CALLBACK, handler_context=handler_context)

    context.api.assert_edit_message_called(
        update, expected_past_meeting_view(inactive_meeting, user_with_settings, page=3)
    )
    # CallbackData.__eq__ ignores the page field, so assert it explicitly on the DELETE button.
    edited_view = context.api.call_args("edit_message").kwargs["view"]
    delete_button = edited_view.keyboard[0][1]
    assert str(delete_button.callback_data).endswith(";page:3")


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.DELETE_PAST_MEETING.with_page(MEETING_ID, 3))], indirect=True
)
async def test_delete_past_meeting_threads_page_into_confirmation(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    inactive_meeting: Meetup,
    handler_context: HandlerContext,
):
    """The delete confirmation must carry the originating page so decline/confirm return to it."""
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(inactive_meeting)

    context, _ = await call_handler(MeetingHandlerId.DELETE_PAST_MEETING_CALLBACK, handler_context=handler_context)

    context.api.assert_edit_message_called(
        update,
        factory.confirmation_view(
            RenderContext(lang=user_with_settings.lang),
            message=MeetingLifecycleMessages.DELETE_CONFIRMATION.get(lang=user_with_settings.lang),
            confirm_callback_data=cb.CONFIRM_DELETE_PAST_MEETING.with_page(MEETING_ID, 3),
            decline_callback_data=cb.DECLINE_DELETE_PAST_MEETING.with_page(MEETING_ID, 3),
        ),
    )
    # CallbackData.__eq__ ignores the page field, so assert it explicitly on both buttons.
    edited_view = context.api.call_args("edit_message").kwargs["view"]
    confirm_button = edited_view.keyboard[0][0]
    decline_button = edited_view.keyboard[-1][-1]
    assert str(confirm_button.callback_data).endswith(";page:3")
    assert str(decline_button.callback_data).endswith(";page:3")


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.CONFIRM_DELETE_PAST_MEETING.with_page(MEETING_ID, 3))], indirect=True
)
async def test_confirm_delete_past_meeting_back_button_returns_to_originating_page(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    inactive_meeting: Meetup,
    handler_context: HandlerContext,
):
    """After deleting from page 3 the success view's Back button must return to page 3."""
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(inactive_meeting)

    context, _ = await call_handler(
        MeetingHandlerId.CONFIRM_DELETE_PAST_MEETING_CALLBACK, handler_context=handler_context
    )

    context.api.assert_edit_message_called(
        update,
        MitupView(
            description=MeetingLifecycleMessages.DELETE_SUCCESS.get(lang=user_with_settings.lang),
            keyboard=[
                [
                    ButtonConfig(
                        text=ButtonMessages.PAST_MEETINGS.back(lang=user_with_settings.lang),
                        callback_data=cb.SHOW_PAST_MEETING_PAGE.with_id(3),
                    )
                ]
            ],
        ),
    )


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.DECLINE_DELETE_PAST_MEETING.with_page(MEETING_ID, 3))], indirect=True
)
async def test_decline_delete_past_meeting_returns_to_originating_page(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    inactive_meeting: Meetup,
    handler_context: HandlerContext,
):
    """Declining a deletion started from page 3 must re-render the detail view for page 3."""
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(inactive_meeting)

    context, _ = await call_handler(
        MeetingHandlerId.DECLINE_DELETE_PAST_MEETING_CALLBACK, handler_context=handler_context
    )

    context.api.assert_edit_message_called(
        update, expected_past_meeting_view(inactive_meeting, user_with_settings, page=3)
    )
    # CallbackData.__eq__ ignores the page field, so assert it explicitly on the DELETE button.
    edited_view = context.api.call_args("edit_message").kwargs["view"]
    delete_button = edited_view.keyboard[0][1]
    assert str(delete_button.callback_data).endswith(";page:3")


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.CONFIRM_DELETE_PAST_MEETING.with_id(MEETING_ID))], indirect=True
)
async def test_confirm_delete_past_meeting_deletes_and_redirects_to_past_meetings(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    inactive_meeting: Meetup,
    handler_context: HandlerContext,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(inactive_meeting)

    context, _ = await call_handler(
        MeetingHandlerId.CONFIRM_DELETE_PAST_MEETING_CALLBACK, handler_context=handler_context
    )

    mock_session.assert_deleted(inactive_meeting)
    context.api.assert_update_meeting_messages_called(meeting=inactive_meeting, was_deleted=True)
    context.api.assert_edit_message_called(
        update,
        MitupView(
            description=MeetingLifecycleMessages.DELETE_SUCCESS.get(lang=user_with_settings.lang),
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
    # Regression (issue #170): the success view must replace the detail message in place, never post
    # a new message that would leave stale buttons bound to the now-deleted meeting id in the chat.
    context.api.assert_send_message_not_called()


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.DECLINE_DELETE_PAST_MEETING.with_id(MEETING_ID))], indirect=True
)
async def test_decline_delete_past_meeting_returns_to_past_meeting_view(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    inactive_meeting: Meetup,
    handler_context: HandlerContext,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(inactive_meeting)

    context, _ = await call_handler(
        MeetingHandlerId.DECLINE_DELETE_PAST_MEETING_CALLBACK, handler_context=handler_context
    )

    mock_session.assert_not_deleted()
    context.api.assert_edit_message_called(update, expected_past_meeting_view(inactive_meeting, user_with_settings))


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.SHOW_PAST_MEETING.with_id(MEETING_ID))], indirect=True
)
async def test_show_past_meeting_silent_when_full_meeting_not_found(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    inactive_meeting: Meetup,
    handler_context: HandlerContext,
):
    """When the user owns the meeting but Meetup.by_id returns None the handler returns silently."""
    # Register the user so guards.current_user succeeds, but do NOT register inactive_meeting
    # so that await Meetup.by_id(session, id, include_inactive=True) returns None.
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(MeetingHandlerId.SHOW_PAST_MEETING_CALLBACK, handler_context=handler_context)

    context.api.assert_method_just_called("edit_message", times=0)


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.CONFIRM_DELETE_PAST_MEETING.with_id(MEETING_ID))], indirect=True
)
async def test_confirm_delete_past_meeting_silent_when_full_meeting_not_found(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    inactive_meeting: Meetup,
    handler_context: HandlerContext,
):
    """When Meetup.by_id returns None the confirm-delete handler returns silently without deleting anything."""
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(
        MeetingHandlerId.CONFIRM_DELETE_PAST_MEETING_CALLBACK, handler_context=handler_context
    )

    mock_session.assert_not_deleted()
    context.api.assert_method_just_called("edit_message", times=0)
    context.api.assert_method_just_called("send_message", times=0)


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.DECLINE_DELETE_PAST_MEETING.with_id(MEETING_ID))], indirect=True
)
async def test_decline_delete_past_meeting_silent_when_full_meeting_not_found(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    inactive_meeting: Meetup,
    handler_context: HandlerContext,
):
    """When Meetup.by_id returns None the decline-delete handler returns silently."""
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(
        MeetingHandlerId.DECLINE_DELETE_PAST_MEETING_CALLBACK, handler_context=handler_context
    )

    context.api.assert_method_just_called("edit_message", times=0)
