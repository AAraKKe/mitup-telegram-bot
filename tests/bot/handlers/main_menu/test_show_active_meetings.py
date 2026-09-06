import re

import pytest
from telegram import Update

from mitup_bot.callback_data import MeetingListSource
from mitup_bot.exceptions import MalformedCallbackData
from mitup_bot.handlers.main_menu.enums import MainMenuHandlerId
from mitup_bot.handlers.main_menu.show_active_meetings import callback_query_show_meetings
from mitup_bot.handlers.main_menu.utils import MEETINGS_PER_PAGE
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Meetup, User
from mitup_bot.utils import ButtonMessages, MeetingDisplayMessages, MeetingListMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.mitup_view import MitupView, PaginatedMitupView
from tests.helpers import HandlerContext, StubMitupContext, UpdateRequest, call_handler, create_meetup
from tests.helpers.stub_db import MockDbSession


def open_callbacks(view: MitupView) -> list[str]:
    return re.findall(r'data="(show;meeting:[^"]*)"', view.message.html)


def expected_view(user: User, meetings: list[Meetup], page_number: int = 1) -> PaginatedMitupView:
    return PaginatedMitupView(
        message=meeting_views.list_heading(ButtonMessages.ACTIVE_MEETINGS, user.lang),
        sections=[
            meeting_views.meeting_list_section(
                meeting,
                cb.SHOW_MEETING.with_page(meeting.db_id, page_number, MeetingListSource.ACTIVE),
                user.lang,
                delete_callback=cb.DELETE_MEETING.with_id(meeting.db_id),
            )
            for meeting in meetings
        ],
        page_size=MEETINGS_PER_PAGE,
        page_number=page_number,
        navigation_callback_data=cb.SHOW_ACTIVE_MEETING_PAGE,
    ).with_context_menu(
        [[ButtonConfig(text=ButtonMessages.MAIN_MENU.back(lang=user.lang), callback_data=cb.MAIN_MENU)]]
    )


async def test_show_meetings_fails_without_callback_query_data(
    mock_session: MockDbSession,
    update: Update,
    context: StubMitupContext,
):
    match = re.match(cb.SHOW_ACTIVE_MEETING_PAGE.pattern, "show;active_meeting_page:")
    assert match is not None

    context.matches = [match]
    with pytest.raises(MalformedCallbackData):
        await callback_query_show_meetings(update, context)


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(1))], indirect=True
)
async def test_show_meetings_lists_every_active_meeting_as_a_section(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    active_meetings = [create_meetup(id=meeting_id) for meeting_id in range(10, 14)]
    user_with_settings.meetups = [*active_meetings, create_meetup(id=14, active=False)]
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_MEETINGS_CALLBACK, handler_context=handler_context)

    context.api.assert_edit_message_called(update, expected_view(user_with_settings, active_meetings))


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(2))], indirect=True
)
async def test_show_meetings_embeds_current_page_in_the_open_chips(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    """Each open chip must encode the page and list it was shown on so the card's back button can
    return to that exact page instead of the main menu."""
    user_with_settings.meetups = [create_meetup(id=meeting_id) for meeting_id in range(10, 18)]
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_MEETINGS_CALLBACK, handler_context=handler_context)

    view = context.api.call_args("edit_message").kwargs["view"]
    assert open_callbacks(view) == [f"show;meeting:{meeting_id};page:2;src:a" for meeting_id in range(15, 18)]


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(9))], indirect=True
)
async def test_show_meetings_clamps_out_of_range_page(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    """A stale page beyond the last one (e.g. from the meeting-inaccessible fallback button after
    the list shrank) clamps to the last page instead of raising."""
    user_with_settings.meetups = [create_meetup(id=meeting_id) for meeting_id in range(10, 14)]
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_MEETINGS_CALLBACK, handler_context=handler_context)

    view = context.api.call_args("edit_message").kwargs["view"]
    assert all(callback.endswith(";page:1;src:a") for callback in open_callbacks(view))


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(1))], indirect=True
)
async def test_show_meetings_without_meetings_to_show_works(
    mock_session: MockDbSession, update: Update, handler_context: HandlerContext, user_with_settings: User
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    user_with_settings.meetups = [create_meetup(10, active=False)]

    context, _ = await call_handler(MainMenuHandlerId.SHOW_MEETINGS_CALLBACK, handler_context=handler_context)

    context.api.assert_answer_callback_query_called(
        update,
        text=MeetingListMessages.ACTIVE_EMPTY_ALERT.text(lang=user_with_settings.lang),
        show_alert=True,
    )
    context.api.assert_edit_message_not_called()


@pytest.mark.parametrize(
    ("blank_title", "meeting_id"),
    [("", 20), ("   ", 21)],
    ids=["empty_title", "whitespace_only_title"],
)
@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(1))], indirect=True
)
async def test_show_meetings_lists_a_blank_titled_meeting_as_untitled(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    blank_title: str,
    meeting_id: int,
):
    """A meeting whose title has no visible text is still the owner's, so the list names it rather
    than hiding it."""
    mock_session.add_object(user_with_settings, "tg_user_id")
    user_with_settings.meetups = [create_meetup(meeting_id, title=blank_title)]

    context, _ = await call_handler(MainMenuHandlerId.SHOW_MEETINGS_CALLBACK, handler_context=handler_context)

    view = context.api.call_args("edit_message").kwargs["view"]
    assert MeetingDisplayMessages.UNTITLED.rich(lang=user_with_settings.lang).text in view.message.text
    assert open_callbacks(view) == [f"show;meeting:{meeting_id};page:1;src:a"]
