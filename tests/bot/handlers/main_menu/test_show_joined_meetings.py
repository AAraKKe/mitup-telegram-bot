import re

import pytest
from telegram import Update

from mitup_bot.callback_data import MeetingListSource
from mitup_bot.exceptions import MalformedCallbackData
from mitup_bot.handlers.main_menu.enums import MainMenuHandlerId
from mitup_bot.handlers.main_menu.show_joined_meetings import callback_query_show_joined_meetings
from mitup_bot.handlers.main_menu.utils import MEETINGS_PER_PAGE
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Meetup, User
from mitup_bot.utils import ButtonMessages, MeetingListMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.mitup_view import MitupView, PaginatedMitupView
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


def host() -> User:
    return create_user(id=999, tg_user_id=9990, first_name="Owner", settings=create_settings(id=2))


def join(user: User, meetings: list[Meetup]) -> None:
    user.joined_links = [create_joined_link(user=user, meetup=meeting) for meeting in meetings]


def open_callbacks(view: MitupView) -> list[str]:
    return re.findall(r'data="(show;meeting:[^"]*)"', view.message.html)


def expected_view(user: User, meetings: list[Meetup], page_number: int = 1) -> PaginatedMitupView:
    return PaginatedMitupView(
        message=meeting_views.list_heading(ButtonMessages.JOINED_MEETINGS, user.lang),
        sections=[
            meeting_views.meeting_list_section(
                meeting, cb.SHOW_MEETING.with_page(meeting.db_id, page_number, MeetingListSource.JOINED), user.lang
            )
            for meeting in meetings
        ],
        page_size=MEETINGS_PER_PAGE,
        page_number=page_number,
        navigation_callback_data=cb.SHOW_JOINED_MEETINGS_PAGE,
    ).with_context_menu(
        [[ButtonConfig(text=ButtonMessages.MAIN_MENU.back(lang=user.lang), callback_data=cb.MAIN_MENU)]]
    )


async def test_show_meetings_fails_without_callback_query_data(
    mock_session: MockDbSession,
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
async def test_show_meetings_lists_every_joined_meeting_as_a_section(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    owner = host()
    joined = [create_meetup(id=meeting_id, owner=owner, title=f"Meeting {meeting_id}") for meeting_id in range(10, 14)]
    join(user_with_settings, joined)
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_JOINED_MEETINGS_CALLBACK, handler_context=handler_context)

    context.api.assert_edit_message_called(update, expected_view(user_with_settings, joined))


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.SHOW_JOINED_MEETINGS_PAGE.with_id(2))], indirect=True
)
async def test_show_meetings_embeds_current_page_in_the_open_chips(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    """Each open chip must encode the page and list it was shown on so the card's back button can
    return to that exact page instead of the main menu."""
    owner = host()
    join(user_with_settings, [create_meetup(id=meeting_id, owner=owner) for meeting_id in range(10, 18)])
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_JOINED_MEETINGS_CALLBACK, handler_context=handler_context)

    view = context.api.call_args("edit_message").kwargs["view"]
    assert open_callbacks(view) == [f"show;meeting:{meeting_id};page:2;src:j" for meeting_id in range(15, 18)]


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
    owner = host()
    join(user_with_settings, [create_meetup(id=meeting_id, owner=owner) for meeting_id in range(10, 14)])
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_JOINED_MEETINGS_CALLBACK, handler_context=handler_context)

    view = context.api.call_args("edit_message").kwargs["view"]
    assert all(callback.endswith(";page:1;src:j") for callback in open_callbacks(view))


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
    joined_meeting = create_meetup(id=7, owner=host(), title="Owner's Meeting")
    join(user_with_settings, [joined_meeting])
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_JOINED_MEETINGS_CALLBACK, handler_context=handler_context)

    context.api.assert_edit_message_called(update, expected_view(user_with_settings, [joined_meeting]))


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.SHOW_JOINED_MEETINGS_PAGE.with_id(1))], indirect=True
)
async def test_show_meetings_filters_out_inactive_meetings(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    owner = host()
    active_meetup = create_meetup(id=10, owner=owner, title="Active Meeting")
    inactive_meetup = create_meetup(id=11, owner=owner, title="Inactive Meeting", active=False)
    join(user_with_settings, [active_meetup, inactive_meetup])
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_JOINED_MEETINGS_CALLBACK, handler_context=handler_context)

    context.api.assert_edit_message_called(update, expected_view(user_with_settings, [active_meetup]))


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.SHOW_JOINED_MEETINGS_PAGE.with_id(1))], indirect=True
)
async def test_show_meetings_shows_empty_state_when_all_joined_meetings_inactive(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    handler_context: HandlerContext,
):
    owner = host()
    join(
        user_with_settings,
        [create_meetup(id=meeting_id, owner=owner, active=False) for meeting_id in range(10, 13)],
    )
    mock_session.add_object(user_with_settings, query_field="tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_JOINED_MEETINGS_CALLBACK, handler_context=handler_context)

    context.api.assert_answer_callback_query_called(
        update,
        text=MeetingListMessages.JOINED_EMPTY_ALERT.text(lang=user_with_settings.lang),
        show_alert=True,
    )
    context.api.assert_edit_message_not_called()


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.SHOW_JOINED_MEETINGS_PAGE.with_id(1))], indirect=True
)
async def test_show_meetings_without_meetings_to_show_works(
    mock_session: MockDbSession, update: Update, handler_context: HandlerContext, user_with_settings: User
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    user_with_settings.meetups = []

    context, _ = await call_handler(MainMenuHandlerId.SHOW_JOINED_MEETINGS_CALLBACK, handler_context=handler_context)

    context.api.assert_answer_callback_query_called(
        update,
        text=MeetingListMessages.JOINED_EMPTY_ALERT.text(lang=user_with_settings.lang),
        show_alert=True,
    )
    context.api.assert_edit_message_not_called()
