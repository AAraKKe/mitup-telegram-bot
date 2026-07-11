import re

import pytest
from telegram import Update

from mitup_bot.exceptions import MalformedCallbackData
from mitup_bot.handlers.main_menu.enums import MainMenuHandlerId
from mitup_bot.handlers.main_menu.show_past_meetings import callback_query_show_past_meeting_page
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import User
from mitup_bot.utils import ButtonMessages, MeetingListMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import RenderContext, factory
from mitup_bot.views.mitup_view import PaginatedMitupView
from tests.helpers import HandlerContext, StubMitupContext, UpdateRequest, call_handler, create_meetup
from tests.helpers.stub_db import MockDbSession


async def test_show_past_meeting_page_fails_without_callback_query_data(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
):
    match = re.match(cb.SHOW_PAST_MEETING_PAGE.pattern, "show;past_meeting_page:")
    assert match is not None

    context.matches = [match]
    with pytest.raises(MalformedCallbackData):
        await callback_query_show_past_meeting_page(update, context)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.PAST_MEETINGS)], indirect=True)
async def test_show_past_meetings_entry_shows_correct_view(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    past_meetings = [create_meetup(id=i, active=False) for i in range(10, 14)]
    user_with_settings.meetups = past_meetings + [create_meetup(id=20, active=True)]
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_PAST_MEETINGS_CALLBACK, handler_context=handler_context)

    expected_buttons = [
        ButtonConfig(text=str(m.title), callback_data=cb.SHOW_PAST_MEETING.with_id(m.db_id)) for m in past_meetings
    ]
    expected_view = PaginatedMitupView(
        description=MeetingListMessages.PAST_DESCRIPTION.get(lang=user_with_settings.lang),
        buttons=expected_buttons,
        page_number=1,
        navigation_callback_data=cb.SHOW_PAST_MEETING_PAGE,
    ).with_context_menu(
        [
            [
                ButtonConfig(
                    text=ButtonMessages.MAIN_MENU.back(lang=user_with_settings.lang),
                    callback_data=cb.MAIN_MENU,
                )
            ]
        ]
    )
    context.api.assert_edit_message_called(update, expected_view)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.SHOW_PAST_MEETING_PAGE.with_id(1))], indirect=True)
async def test_show_past_meetings_page_navigation_shows_correct_view(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    past_meetings = [create_meetup(id=i, active=False) for i in range(10, 14)]
    user_with_settings.meetups = past_meetings
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_PAST_MEETING_PAGE_CALLBACK, handler_context=handler_context)

    expected_buttons = [
        ButtonConfig(text=str(m.title), callback_data=cb.SHOW_PAST_MEETING.with_id(m.db_id)) for m in past_meetings
    ]
    expected_view = PaginatedMitupView(
        description=MeetingListMessages.PAST_DESCRIPTION.get(lang=user_with_settings.lang),
        buttons=expected_buttons,
        page_number=1,
        navigation_callback_data=cb.SHOW_PAST_MEETING_PAGE,
    ).with_context_menu(
        [
            [
                ButtonConfig(
                    text=ButtonMessages.MAIN_MENU.back(lang=user_with_settings.lang),
                    callback_data=cb.MAIN_MENU,
                )
            ]
        ]
    )
    context.api.assert_edit_message_called(update, expected_view)


def past_meeting_item_buttons(view: PaginatedMitupView) -> list[ButtonConfig]:
    return [
        button for row in view.keyboard for button in row if str(button.callback_data).startswith("show;past_meeting:")
    ]


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.SHOW_PAST_MEETING_PAGE.with_id(2))], indirect=True)
async def test_show_past_meetings_embeds_current_page_in_item_buttons(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    """Each list item must encode the page it was shown on so the detail view can return to it."""
    user_with_settings.meetups = [create_meetup(id=i, active=False) for i in range(10, 18)]
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_PAST_MEETING_PAGE_CALLBACK, handler_context=handler_context)

    view = context.api.call_args("edit_message").kwargs["view"]
    item_buttons = past_meeting_item_buttons(view)
    assert item_buttons
    assert all(str(button.callback_data).endswith(";page:2") for button in item_buttons)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.SHOW_PAST_MEETING_PAGE.with_id(9))], indirect=True)
async def test_show_past_meetings_clamps_out_of_range_page(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    """A stale page beyond the last one (e.g. after deleting its only item) clamps to the last page
    instead of raising."""
    user_with_settings.meetups = [create_meetup(id=i, active=False) for i in range(10, 14)]
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_PAST_MEETING_PAGE_CALLBACK, handler_context=handler_context)

    view = context.api.call_args("edit_message").kwargs["view"]
    item_buttons = past_meeting_item_buttons(view)
    assert item_buttons
    assert all(str(button.callback_data).endswith(";page:1") for button in item_buttons)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.PAST_MEETINGS)], indirect=True)
async def test_show_past_meetings_excludes_active_meetings(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    """Active meetings must not appear in the past meetings list."""
    past = create_meetup(id=10, active=False)
    active = create_meetup(id=11, active=True)
    user_with_settings.meetups = [past, active]
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_PAST_MEETINGS_CALLBACK, handler_context=handler_context)

    expected_view = PaginatedMitupView(
        description=MeetingListMessages.PAST_DESCRIPTION.get(lang=user_with_settings.lang),
        buttons=[ButtonConfig(text=str(past.title), callback_data=cb.SHOW_PAST_MEETING.with_id(past.db_id))],
        page_number=1,
        navigation_callback_data=cb.SHOW_PAST_MEETING_PAGE,
    ).with_context_menu(
        [
            [
                ButtonConfig(
                    text=ButtonMessages.MAIN_MENU.back(lang=user_with_settings.lang),
                    callback_data=cb.MAIN_MENU,
                )
            ]
        ]
    )
    context.api.assert_edit_message_called(update, expected_view)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.PAST_MEETINGS)], indirect=True)
async def test_show_past_meetings_without_past_meetings_shows_main_menu(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    user_with_settings.meetups = [create_meetup(id=10, active=True)]
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_PAST_MEETINGS_CALLBACK, handler_context=handler_context)

    expected_view = factory.main_menu_view(
        RenderContext(lang=user_with_settings.lang),
        message=MeetingListMessages.PAST_EMPTY.get(lang=user_with_settings.lang),
    )
    context.api.assert_edit_message_called(update, expected_view)
