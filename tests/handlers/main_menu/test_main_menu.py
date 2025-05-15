import re

import pytest
from telegram import Update

from mitup_bot.custom_context import ContextId, MitupContext
from mitup_bot.exceptions import MalformedCallbackData
from mitup_bot.handlers.edit_settings.entry import callback_query_cancel_settings, callback_query_settings
from mitup_bot.handlers.main_menu.enums import MainMenuHandlerId  # For call_handler with SHOW_MEETINGS_CALLBACK
from mitup_bot.handlers.main_menu.show_active_meetings import callback_query_show_meetings

# Updated imports for handlers and enums:
from mitup_bot.handlers.main_menu.show_main_menu import callback_query_main_menu
from mitup_bot.handlers.meeting.create_meeting import (
    ConversationMeetingState,  # For create_meeting context
    callback_query_cancel_meeting,
    callback_query_create_meeting,
)
from mitup_bot.models import User
from mitup_bot.monitoring import Feature
from mitup_bot.utils import MeetingMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages
from mitup_bot.views import factory
from mitup_bot.views.mitup_view import ButtonConfig, PaginatedMitupView
from tests.helpers import MockApi, StubMitupApp, StubMitupContext, UpdateRequest, call_handler, create_meetup
from tests.helpers.stub_db import MockDbSession


@pytest.fixture
def api():
    with MockApi.start("mitup_bot.handlers.main_menu.show_main_menu") as api:
        yield api


async def test_callback_query_settings_is_called_with_settings_view(
    update: Update, context: StubMitupContext, api: MockApi, user_with_settings: User, mock_session: MockDbSession
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    await callback_query_settings(update, context)

    api.assert_edit_message_called(context, update, factory.settings_view(lang=user_with_settings.lang))


async def test_callback_query_show_main_menu(
    update: Update, context: StubMitupContext, api: MockApi, user_with_settings: User, mock_session: MockDbSession
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    await callback_query_main_menu(update, context)

    api.assert_edit_message_called(context, update, factory.main_menu_view(lang=user_with_settings.lang))


async def test_callback_query_cancel_meeting_calls_to_main_menu_view(  # Renamed for clarity
    update: Update,
    context: StubMitupContext,
    api: MockApi,
    user_with_settings: User,
    mock_session: MockDbSession,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    match = re.match(cb.CANCEL_CREATE_MEETING.pattern, str(cb.CANCEL_CREATE_MEETING))
    assert match is not None

    context.matches = [match]  # callback_query_cancel_meeting might rely on context.match

    await callback_query_cancel_meeting(update, context)
    await context.flush_metrics()

    api.assert_edit_message_called(context, update, factory.main_menu_view(lang=user_with_settings.lang))
    context.metrics_engine.assert_metrics_emited(
        names=["Cancel"],
        values=[1],
        dimensions={"Feature": Feature.CREATE_MEETING},
        add_handler_dimensions=False,  # Assuming this was intentional
    )


async def test_callback_query_cancel_setting_calls_to_settings_view(
    update: Update, context: StubMitupContext, api: MockApi, user_with_settings: User, mock_session: MockDbSession
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    await callback_query_cancel_settings(update, context)

    api.assert_send_message_called(context, update, factory.settings_view(lang=user_with_settings.lang))


async def test_callback_query_create_meeting_calls_to_create_meeting_view(
    update: Update, context: StubMitupContext, api: MockApi, user_with_settings: User, mock_session: MockDbSession
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    await callback_query_create_meeting(update, context)

    api.assert_edit_message_called(context, update, factory.create_meeting_view(lang=user_with_settings.lang))


async def test_callback_query_create_meeting_return_the_correct_state(
    update: Update, context: StubMitupContext, api: MockApi, user_with_settings: User, mock_session: MockDbSession
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    result = await callback_query_create_meeting(update, context)

    assert result == ConversationMeetingState.TITLE


async def test_callback_query_show_meetings_fails_without_callback_query_data(
    mock_session: MockDbSession,  # Added mock_session as it's a common fixture
    update: Update,
    context: StubMitupContext,
):
    match = re.match(cb.SHOW_ACTIVE_MEETING_PAGE.pattern, "show;active_meeting_page:")
    assert match is not None

    context.matches = [match]
    with pytest.raises(MalformedCallbackData):
        await callback_query_show_meetings(update, context)


async def test_callback_query_show_meetings_use_correct_view(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    api: MockApi,
):
    match = re.match(cb.SHOW_ACTIVE_MEETING_PAGE.pattern, "show;active_meeting_page:1")
    assert match is not None

    context.matches = [match]
    mock_session.add_object(user_with_settings, "tg_user_id")
    user_with_settings.meetups += [create_meetup(10), create_meetup(11), create_meetup(12), create_meetup(13)]

    await callback_query_show_meetings(update, context)

    user_meetings_buttons: list[ButtonConfig] = [
        ButtonConfig(text=str(meeting.title), callback_data=cb.SHOW_MEETING.with_id(int(meeting.id)))  # type: ignore
        for meeting in user_with_settings.meetups
    ]

    expected_view = PaginatedMitupView(
        description=MeetingMessages.ACTIVE.get(lang=user_with_settings.lang),
        buttons=user_meetings_buttons,
        page_number=1,
        column_size=2,
        row_size=2,
        navigation_callback_data=cb.SHOW_ACTIVE_MEETING_PAGE,
    ).with_context_menu(
        [
            [
                ButtonConfig(
                    text=f"{ButtonMessages.GO_BACK.get()}{ButtonMessages.MAIN_MENU.get(lang=user_with_settings.lang)}",
                    callback_data=cb.MAIN_MENU,
                )
            ]
        ]
    )
    api.assert_edit_message_called(context, update, expected_view)


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(1))], indirect=True
)
async def test_callback_query_show_meetings_without_meetings_to_show_works(
    mock_session: MockDbSession, update: Update, app: StubMitupApp, user_with_settings: User, api: MockApi
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    user_with_settings.meetups = []

    # Use MainMenuHandlerId for call_handler
    context, _ = await call_handler(update, app, MainMenuHandlerId.SHOW_MEETINGS_CALLBACK)

    expected_view = factory.main_menu_view(
        MeetingMessages.NO_MEETINGS_FOUND.get(
            lang=user_with_settings.lang,  # ensure lang is passed
            new_meeting_button=ButtonMessages.NEW_MEETING.get(lang=user_with_settings.lang),
        )
    )

    api.assert_edit_message_called(context, update, expected_view)


async def test_callback_query_main_menu_delete_user_data_related_with_edit_meetings(
    update: Update, context: MitupContext, user_with_settings: User, mock_session: MockDbSession
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    assert context.user_data is not None

    context.store_meeting_id(ContextId.EDIT_MEETING_TITLE, 1)
    # Accessing registry directly might be an issue if internal structure changes, but following original test
    assert context.user_data.registry[ContextId.EDIT_MEETING_TITLE].meeting_id == 1

    await callback_query_main_menu(update, context)

    assert ContextId.EDIT_MEETING_TITLE not in context.user_data.registry
