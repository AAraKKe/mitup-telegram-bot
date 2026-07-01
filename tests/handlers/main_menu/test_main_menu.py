import re

from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot.custom_context import ContextId
from mitup_bot.handlers.edit_settings.entry import callback_query_cancel_settings, callback_query_settings

# Updated imports for handlers and enums:
from mitup_bot.handlers.main_menu.show_main_menu import callback_query_main_menu
from mitup_bot.handlers.meeting.create_meeting import (
    ConversationMeetingState,  # For create_meeting context
    build_datetime_link,
    callback_query_cancel_meeting,
    callback_query_create_meeting,
)
from mitup_bot.models import User
from mitup_bot.monitoring import Feature
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import factory
from tests.helpers import StubMitupContext
from tests.helpers.monitoring import MetricAssertions
from tests.helpers.stub_db import MockDbSession


async def test_settings_is_called_with_settings_view(
    update: Update, context: StubMitupContext, user_with_settings: User, mock_session: MockDbSession
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    await callback_query_settings(update, context)

    context.api.assert_edit_message_called(update, factory.settings_view(lang=user_with_settings.lang))


async def test_show_main_menu(
    update: Update, context: StubMitupContext, user_with_settings: User, mock_session: MockDbSession
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    await callback_query_main_menu(update, context)

    context.api.assert_edit_message_called(update, factory.main_menu_view(lang=user_with_settings.lang))


async def test_cancel_meeting_calls_to_main_menu_view(  # Renamed for clarity
    update: Update,
    context: StubMitupContext,
    metrics: MetricAssertions,
    user_with_settings: User,
    mock_session: MockDbSession,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    match = re.match(cb.CANCEL_CREATE_MEETING.pattern, str(cb.CANCEL_CREATE_MEETING))
    assert match is not None

    context.matches = [match]  # callback_query_cancel_meeting might rely on context.match

    await callback_query_cancel_meeting(update, context)
    await context.flush_metrics()

    context.api.assert_edit_message_called(update, factory.main_menu_view(lang=user_with_settings.lang))
    metrics.assert_emitted(
        name="Cancel",
        value=1,
        dimensions={"Feature": str(Feature.CREATE_MEETING)},
    )


async def test_cancel_setting_calls_to_settings_view(
    update: Update, context: StubMitupContext, user_with_settings: User, mock_session: MockDbSession
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    await callback_query_cancel_settings(update, context)

    context.api.assert_edit_message_called(update, factory.settings_view(lang=user_with_settings.lang))


async def test_cancel_setting_edits_prompt_in_place_without_posting_new_message(
    update: Update, context: StubMitupContext, user_with_settings: User, mock_session: MockDbSession
):
    # Regression for issue #175: cancelling a setting must replace the prompt in
    # place. Posting a new message left the stale prompt (with its now-inert
    # Cancel button) on screen and appended a duplicate "Settings" message below.
    mock_session.add_object(user_with_settings, "tg_user_id")

    await callback_query_cancel_settings(update, context)

    context.api.assert_send_message_not_called()


async def test_cancel_setting_ends_conversation(
    update: Update, context: StubMitupContext, user_with_settings: User, mock_session: MockDbSession
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    result = await callback_query_cancel_settings(update, context)

    assert result == ConversationHandler.END


async def test_create_meeting_calls_to_create_meeting_view(
    update: Update, context: StubMitupContext, user_with_settings: User, mock_session: MockDbSession
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    await callback_query_create_meeting(update, context)

    context.api.assert_edit_message_called(
        update, factory.create_meeting_view(lang=user_with_settings.lang, datetime_link=build_datetime_link())
    )


async def test_create_meeting_return_the_correct_state(
    update: Update, context: StubMitupContext, user_with_settings: User, mock_session: MockDbSession
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    result = await callback_query_create_meeting(update, context)

    assert result == ConversationMeetingState.TITLE


async def test_main_menu_delete_user_data_related_with_edit_meetings(
    update: Update, context: StubMitupContext, user_with_settings: User, mock_session: MockDbSession
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    assert context.user_data is not None

    context.store_meeting_id(ContextId.EDIT_MEETING_TITLE, 1)
    # Accessing registry directly might be an issue if internal structure changes, but following original test
    assert context.user_data.registry[ContextId.EDIT_MEETING_TITLE].meeting_id == 1

    await callback_query_main_menu(update, context)

    assert ContextId.EDIT_MEETING_TITLE not in context.user_data.registry
