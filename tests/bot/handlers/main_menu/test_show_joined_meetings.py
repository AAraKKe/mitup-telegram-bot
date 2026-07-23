import re

import pytest
from telegram import Update

from mitup_bot.exceptions import MalformedCallbackData
from mitup_bot.handlers.main_menu.enums import MainMenuHandlerId
from mitup_bot.handlers.main_menu.show_joined_meetings import callback_query_show_joined_meetings
from mitup_bot.keyboards import ButtonConfig

# Updated imports for handlers and enums:
from mitup_bot.models import User
from mitup_bot.utils import ButtonMessages, MeetingListMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import RenderContext, factory
from mitup_bot.views.mitup_view import PaginatedMitupView
from tests.helpers import (
    HandlerContext,
    StubMitupContext,
    UpdateRequest,
    call_handler,
    create_joined_link,
    create_meetup,
    create_settings,
    create_user,
)
from tests.helpers.stub_db import MockDbSession


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
    handler_context: HandlerContext,
):
    # We add some meetings to the user joined_links so that the view is not empty
    meetups_to_join = [create_meetup(id=i, owner=user_with_settings, title=f"Test Meeting {i}") for i in range(10, 14)]
    user_with_settings.joined_links = [
        create_joined_link(user=user_with_settings, meetup=meetup) for meetup in meetups_to_join
    ]
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_JOINED_MEETINGS_CALLBACK, handler_context=handler_context)

    user_meetings_buttons: list[ButtonConfig] = [
        ButtonConfig(text=link.meetup.plain_title, callback_data=cb.SHOW_MEETING.with_id(link.meetup.db_id))
        for link in user_with_settings.joined_links
    ]

    expected_view = PaginatedMitupView(
        description=MeetingListMessages.JOINED_DESCRIPTION.get(lang=user_with_settings.lang),
        buttons=user_meetings_buttons,
        page_number=1,
        column_size=2,
        row_size=2,
        navigation_callback_data=cb.SHOW_JOINED_MEETINGS_PAGE,
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


def joined_meeting_item_buttons(view: PaginatedMitupView) -> list[ButtonConfig]:
    return [button for row in view.keyboard for button in row if str(button.callback_data).startswith("show;meeting:")]


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.SHOW_JOINED_MEETINGS_PAGE.with_id(2))], indirect=True
)
async def test_show_meetings_embeds_current_page_in_item_buttons(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    """Each joined-list item must encode the page and list it was shown on so the detail view's
    back button can return to that exact page instead of the main menu."""
    meetups_to_join = [create_meetup(id=i, title=f"Meeting {i}") for i in range(10, 18)]
    user_with_settings.joined_links = [
        create_joined_link(user=user_with_settings, meetup=meetup) for meetup in meetups_to_join
    ]
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_JOINED_MEETINGS_CALLBACK, handler_context=handler_context)

    view = context.api.call_args("edit_message").kwargs["view"]
    item_buttons = joined_meeting_item_buttons(view)
    assert item_buttons
    assert all(str(button.callback_data).endswith(";page:2;src:j") for button in item_buttons)


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.SHOW_JOINED_MEETINGS_PAGE.with_id(9))], indirect=True
)
async def test_show_meetings_clamps_out_of_range_page(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    """A stale page beyond the last one (e.g. from a detail back button after the list shrank)
    clamps to the last page instead of raising."""
    meetups_to_join = [create_meetup(id=i, title=f"Meeting {i}") for i in range(10, 14)]
    user_with_settings.joined_links = [
        create_joined_link(user=user_with_settings, meetup=meetup) for meetup in meetups_to_join
    ]
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_JOINED_MEETINGS_CALLBACK, handler_context=handler_context)

    view = context.api.call_args("edit_message").kwargs["view"]
    item_buttons = joined_meeting_item_buttons(view)
    assert item_buttons
    assert all(str(button.callback_data).endswith(";page:1;src:j") for button in item_buttons)


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.SHOW_JOINED_MEETINGS_PAGE.with_id(1))], indirect=True
)
async def test_show_meetings_wires_non_owned_joined_meeting_to_show_meeting(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    """A meeting the user joined but does not own is listed and wired to SHOW_MEETING (issue #166 entry point)."""
    owner = create_user(id=999, tg_user_id=9990, first_name="Owner", settings=create_settings(id=2))
    joined_meeting = create_meetup(id=7, owner=owner, title="Owner's Meeting")
    user_with_settings.joined_links = [create_joined_link(user=user_with_settings, meetup=joined_meeting)]
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_JOINED_MEETINGS_CALLBACK, handler_context=handler_context)

    expected_view = PaginatedMitupView(
        description=MeetingListMessages.JOINED_DESCRIPTION.get(lang=user_with_settings.lang),
        buttons=[
            ButtonConfig(
                text="Owner's Meeting",
                callback_data=cb.SHOW_MEETING.with_id(7),
            )
        ],
        page_number=1,
        column_size=2,
        row_size=2,
        navigation_callback_data=cb.SHOW_JOINED_MEETINGS_PAGE,
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


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.SHOW_JOINED_MEETINGS_PAGE.with_id(1))], indirect=True
)
async def test_show_meetings_filters_out_inactive_meetings(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    active_meetup = create_meetup(id=10, title="Active Meeting")
    inactive_meetup = create_meetup(id=11, title="Inactive Meeting", active=False)
    user_with_settings.joined_links = [
        create_joined_link(user=user_with_settings, meetup=active_meetup),
        create_joined_link(user=user_with_settings, meetup=inactive_meetup),
    ]
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_JOINED_MEETINGS_CALLBACK, handler_context=handler_context)

    expected_view = PaginatedMitupView(
        description=MeetingListMessages.JOINED_DESCRIPTION.get(lang=user_with_settings.lang),
        buttons=[
            ButtonConfig(
                text=active_meetup.plain_title,
                callback_data=cb.SHOW_MEETING.with_id(active_meetup.db_id),
            )
        ],
        page_number=1,
        column_size=2,
        row_size=2,
        navigation_callback_data=cb.SHOW_JOINED_MEETINGS_PAGE,
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


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.SHOW_JOINED_MEETINGS_PAGE.with_id(1))], indirect=True
)
async def test_show_meetings_shows_empty_state_when_all_joined_meetings_inactive(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    inactive_meetups = [create_meetup(id=i, title=f"Inactive {i}", active=False) for i in range(10, 13)]
    user_with_settings.joined_links = [
        create_joined_link(user=user_with_settings, meetup=meetup) for meetup in inactive_meetups
    ]
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_JOINED_MEETINGS_CALLBACK, handler_context=handler_context)

    expected_view = factory.main_menu_view(
        RenderContext(lang=user_with_settings.lang),
        message=MeetingListMessages.JOINED_EMPTY.get(lang=user_with_settings.lang),
    )
    context.api.assert_edit_message_called(update, expected_view)


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.SHOW_JOINED_MEETINGS_PAGE.with_id(1))], indirect=True
)
async def test_show_meetings_without_meetings_to_show_works(
    mock_session: MockDbSession, update: Update, handler_context: HandlerContext, user_with_settings: User
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    user_with_settings.meetups = []

    context, _ = await call_handler(MainMenuHandlerId.SHOW_JOINED_MEETINGS_CALLBACK, handler_context=handler_context)

    expected_view = factory.main_menu_view(
        RenderContext(lang=user_with_settings.lang),
        message=MeetingListMessages.JOINED_EMPTY.get(lang=user_with_settings.lang),
    )

    context.api.assert_edit_message_called(update, expected_view)
