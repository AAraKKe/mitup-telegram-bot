import re

import pytest
from telegram import Update

from mitup_bot.exceptions import MalformedCallbackData
from mitup_bot.handlers.main_menu.enums import MainMenuHandlerId
from mitup_bot.handlers.main_menu.show_active_meetings import callback_query_show_meetings
from mitup_bot.models import User
from mitup_bot.utils import MeetingMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages
from mitup_bot.views import factory
from mitup_bot.views.mitup_view import ButtonConfig, PaginatedMitupView
from tests.helpers import StubMitupApp, StubMitupContext, UpdateRequest, call_handler, create_meetup
from tests.helpers.stub_db import MockDbSession


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
):
    match = re.match(cb.SHOW_ACTIVE_MEETING_PAGE.pattern, "show;active_meeting_page:1")
    assert match is not None

    context.matches = [match]
    mock_session.add_object(user_with_settings, "tg_user_id")
    user_with_settings.meetups += [
        create_meetup(10),
        create_meetup(11),
        create_meetup(12),
        create_meetup(13),
        create_meetup(14, active=False),
    ]

    await callback_query_show_meetings(update, context)

    user_meetings_buttons: list[ButtonConfig] = [
        ButtonConfig(text=str(meeting.title), callback_data=cb.SHOW_MEETING.with_id(meeting.db_id))
        for meeting in user_with_settings.meetups
        if meeting.active
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
                    text=ButtonMessages.MAIN_MENU.back(lang=user_with_settings.lang),
                    callback_data=cb.MAIN_MENU,
                )
            ]
        ]
    )
    context.api.assert_edit_message_called(update, expected_view)


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(1))], indirect=True
)
async def test_show_meetings_without_meetings_to_show_works(
    mock_session: MockDbSession, update: Update, app: StubMitupApp, user_with_settings: User
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    user_with_settings.meetups = [create_meetup(10, active=False)]

    # Use MainMenuHandlerId for call_handler
    context, _ = await call_handler(MainMenuHandlerId.SHOW_MEETINGS_CALLBACK, update=update, app=app)

    expected_view = factory.main_menu_view(
        lang=user_with_settings.lang,
        message=MeetingMessages.NO_MEETINGS_FOUND.get(
            lang=user_with_settings.lang,
            new_meeting_button=ButtonMessages.NEW_MEETING.get(lang=user_with_settings.lang),
        ),
    )

    context.api.assert_edit_message_called(update, expected_view)


@pytest.mark.parametrize(
    ("blank_title", "meeting_id"),
    [
        ("", 20),  # empty string
        ("   ", 21),  # whitespace-only
    ],
    ids=["empty_title", "whitespace_only_title"],
)
@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(1))], indirect=True
)
async def test_show_meetings_skips_meetings_with_blank_titles(
    mock_session: MockDbSession,
    update: Update,
    app: StubMitupApp,
    user_with_settings: User,
    blank_title: str,
    meeting_id: int,
):
    """Active meetings whose title is blank or whitespace-only must not appear as buttons.

    This is the guard added in Phase 2 to handle legacy meetings created when the title
    could be stripped to an empty string by the old date-entity logic.
    """
    mock_session.add_object(user_with_settings, "tg_user_id")
    valid_meeting = create_meetup(22, title="Valid meeting")
    blank_meeting = create_meetup(meeting_id, title=blank_title)
    user_with_settings.meetups = [valid_meeting, blank_meeting]

    context, _ = await call_handler(MainMenuHandlerId.SHOW_MEETINGS_CALLBACK, update=update, app=app)

    # Only the meeting with a non-blank title should appear as a button.
    expected_buttons = [
        ButtonConfig(
            text=str(valid_meeting.title),
            callback_data=cb.SHOW_MEETING.with_id(valid_meeting.db_id),
        )
    ]
    expected_view = PaginatedMitupView(
        description=MeetingMessages.ACTIVE_MEETINGS_PAGE.get(lang=user_with_settings.lang),
        buttons=expected_buttons,
        page_number=1,
        navigation_callback_data=cb.SHOW_ACTIVE_MEETING_PAGE,
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
    "update", [UpdateRequest(callback_query=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(1))], indirect=True
)
async def test_show_meetings_falls_back_to_no_meetings_view_when_all_titles_are_blank(
    mock_session: MockDbSession,
    update: Update,
    app: StubMitupApp,
    user_with_settings: User,
):
    """When all active meetings have blank titles the handler must show the 'no meetings' view
    rather than an empty paginated list.
    """
    mock_session.add_object(user_with_settings, "tg_user_id")
    # Both meetings are active but have blank titles — neither should appear as a button.
    user_with_settings.meetups = [
        create_meetup(30, title=""),
        create_meetup(31, title="   "),
    ]

    context, _ = await call_handler(MainMenuHandlerId.SHOW_MEETINGS_CALLBACK, update=update, app=app)

    expected_view = factory.main_menu_view(
        lang=user_with_settings.lang,
        message=MeetingMessages.NO_MEETINGS_FOUND.get(
            lang=user_with_settings.lang,
            new_meeting_button=ButtonMessages.NEW_MEETING.get(lang=user_with_settings.lang),
        ),
    )
    context.api.assert_edit_message_called(update, expected_view)
