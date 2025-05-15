import re
from typing import cast

import pytest
from telegram import Update

from mitup_bot.exceptions import MalformedCallbackData
from mitup_bot.handlers.main_menu.enums import MainMenuHandlerId
from mitup_bot.handlers.main_menu.show_joined_meetings import callback_query_show_joined_meetings

# Updated imports for handlers and enums:
from mitup_bot.models import User
from mitup_bot.utils import MeetingMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages
from mitup_bot.views import factory
from mitup_bot.views.mitup_view import ButtonConfig, PaginatedMitupView
from tests.helpers import (
    MockApi,
    StubMitupApp,
    StubMitupContext,
    UpdateRequest,
    call_handler,
    create_joined_link,
    create_meetup,
)
from tests.helpers.stub_db import MockDbSession


@pytest.fixture
def api():
    with MockApi.start("mitup_bot.handlers.main_menu.show_joined_meetings") as api:
        yield api


async def test_show_meetings_fails_without_callback_query_data(
    mock_session: MockDbSession,  # Added mock_session as it's a common fixture
    update: Update,
    context: StubMitupContext,
):
    match = re.match(cb.SHOW_JOINED_MEETINGS_PAGE.pattern, "show;joined_meetings:")
    assert match is not None

    context.matches = [match]
    with pytest.raises(MalformedCallbackData):
        await callback_query_show_joined_meetings(update, context)


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.SHOW_JOINED_MEETINGS_PAGE.with_id(1))], indirect=True
)
async def test_show_meetings_use_correct_view(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    api: MockApi,
    app: StubMitupApp,
):
    # We add some meetings to the user joined_links so that the view is not empty
    meetups_to_join = [create_meetup(id=i, owner=user_with_settings, title=f"Test Meeting {i}") for i in range(10, 14)]
    user_with_settings.joined_links = [
        create_joined_link(user=user_with_settings, meetup=meetup) for meetup in meetups_to_join
    ]
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, _ = await call_handler(update, app, MainMenuHandlerId.SHOW_JOINED_MEETINGS_CALLBACK)

    user_meetings_buttons: list[ButtonConfig] = [
        ButtonConfig(text=str(link.meetup.title), callback_data=cb.SHOW_MEETING.with_id(cast(int, link.meetup.id)))
        for link in user_with_settings.joined_links
    ]

    expected_view = PaginatedMitupView(
        description=MeetingMessages.JOINED_MEETINGS_PAGE.get(lang=user_with_settings.lang),
        buttons=user_meetings_buttons,
        page_number=1,
        column_size=2,
        row_size=2,
        navigation_callback_data=cb.SHOW_JOINED_MEETINGS_PAGE,
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
    "update", [UpdateRequest(callback_query=cb.SHOW_JOINED_MEETINGS_PAGE.with_id(1))], indirect=True
)
async def test_show_meetings_without_meetings_to_show_works(
    mock_session: MockDbSession, update: Update, app: StubMitupApp, user_with_settings: User, api: MockApi
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    user_with_settings.meetups = []

    context, _ = await call_handler(update, app, MainMenuHandlerId.SHOW_JOINED_MEETINGS_CALLBACK)

    expected_view = factory.main_menu_view(
        lang=user_with_settings.lang,
        message=MeetingMessages.NO_JOINED_MEETINGS.get(lang=user_with_settings.lang),
    )

    api.assert_edit_message_called(context, update, expected_view)
