import re

import pytest
from telegram import Update

from mitup_bot.exceptions import MalformedCallbackData
from mitup_bot.handlers.main_menu.enums import MainMenuHandlerId  # For call_handler with SHOW_MEETINGS_CALLBACK
from mitup_bot.handlers.main_menu.show_active_meetings import callback_query_show_meetings

# Updated imports for handlers and enums:
from mitup_bot.models import User
from mitup_bot.utils import MeetingMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages
from mitup_bot.views import factory
from mitup_bot.views.mitup_view import ButtonConfig, PaginatedMitupView
from tests.helpers import MockApi, StubMitupApp, StubMitupContext, UpdateRequest, call_handler, create_meetup
from tests.helpers.stub_db import MockDbSession


@pytest.fixture
def api():
    with MockApi.start("mitup_bot.handlers.main_menu.show_active_meetings") as api:
        yield api


async def test_show_meetings_fails_without_callback_query_data(
    mock_session: MockDbSession,  # Added mock_session as it's a common fixture
    update: Update,
    context: StubMitupContext,
):
    match = re.match(cb.SHOW_ACTIVE_MEETING_PAGE.pattern, "show;active_meeting_page:")
    assert match is not None

    context.matches = [match]
    with pytest.raises(MalformedCallbackData):
        await callback_query_show_meetings(update, context)


async def test_show_meetings_use_correct_view(
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
        description=MeetingMessages.ACTIVE_MEETINGS_PAGE.get(lang=user_with_settings.lang),
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
async def test_show_meetings_without_meetings_to_show_works(
    mock_session: MockDbSession, update: Update, app: StubMitupApp, user_with_settings: User, api: MockApi
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    user_with_settings.meetups = []

    # Use MainMenuHandlerId for call_handler
    context, _ = await call_handler(update, app, MainMenuHandlerId.SHOW_MEETINGS_CALLBACK)

    expected_view = factory.main_menu_view(
        lang=user_with_settings.lang,
        message=MeetingMessages.NO_MEETINGS_FOUND.get(
            lang=user_with_settings.lang,  # ensure lang is passed
            new_meeting_button=ButtonMessages.NEW_MEETING.get(lang=user_with_settings.lang),
        ),
    )

    api.assert_edit_message_called(context, update, expected_view)
