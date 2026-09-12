import datetime as dt
import re

import pytest
from telegram import Update

from mitup_bot.exceptions import MalformedCallbackData
from mitup_bot.handlers.main_menu.enums import MainMenuHandlerId
from mitup_bot.handlers.main_menu.show_past_meetings import callback_query_show_past_meeting_page
from mitup_bot.handlers.main_menu.utils import MEETINGS_PER_PAGE
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Meetup, User
from mitup_bot.models.users import past_meetings_count_statement
from mitup_bot.utils import ButtonMessages, MeetingLifecycleMessages, MeetingListMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import RenderContext, factory
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.mitup_view import MitupView, PaginatedMitupView
from tests.helpers import (
    HandlerContext,
    StubMitupContext,
    UpdateRequest,
    call_handler,
    create_joined_link,
    create_meetup,
    create_user,
)
from tests.helpers.stub_db import MockDbSession


def open_callbacks(view: MitupView) -> list[str]:
    return re.findall(r'data="(show;past_meeting:[^"]*)"', view.message.html)


def expected_view(user: User, meetings: list[Meetup], page_number: int = 1) -> PaginatedMitupView:
    return PaginatedMitupView(
        message=meeting_views.list_heading(ButtonMessages.PAST_MEETINGS, user.lang),
        sections=[
            meeting_views.meeting_list_section(
                meeting,
                cb.SHOW_PAST_MEETING.with_page(meeting.db_id, page_number),
                user.lang,
                delete_callback=cb.DELETE_PAST_MEETING.with_page(meeting.db_id, page_number),
                with_deletion_notice=True,
            )
            for meeting in meetings
        ],
        page_size=MEETINGS_PER_PAGE,
        page_number=page_number,
        navigation_callback_data=cb.SHOW_PAST_MEETING_PAGE,
    ).with_context_menu(
        [
            [
                ButtonConfig(
                    text=ButtonMessages.DELETE_ALL_PAST_MEETINGS.text(lang=user.lang),
                    callback_data=cb.DELETE_ALL_PAST_MEETINGS,
                    style="danger",
                )
            ],
            [ButtonConfig(text=ButtonMessages.MAIN_MENU.back(lang=user.lang), callback_data=cb.MAIN_MENU)],
        ]
    )


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
async def test_show_past_meetings_entry_lists_every_past_meeting_as_a_section(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    past_meetings = [create_meetup(id=meeting_id, active=False) for meeting_id in range(10, 14)]
    user_with_settings.meetups = [*past_meetings, create_meetup(id=20, active=True)]
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_PAST_MEETINGS_CALLBACK, handler_context=handler_context)

    context.api.assert_edit_message_called(update, expected_view(user_with_settings, past_meetings))


def expired_days_ago(days: int) -> dt.datetime:
    return dt.datetime.now(dt.UTC) - dt.timedelta(days=days)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.PAST_MEETINGS)], indirect=True)
async def test_show_past_meetings_lists_the_nearest_deletion_first(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    """The list is read top down, so the meetings running out of time have to open it."""
    almost_gone = create_meetup(id=12, active=False, expiration_time=expired_days_ago(80))
    still_waiting = create_meetup(id=11, active=False, expiration_time=expired_days_ago(10))
    never_stamped = create_meetup(id=10, active=False)
    user_with_settings.meetups = [never_stamped, still_waiting, almost_gone]
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_PAST_MEETINGS_CALLBACK, handler_context=handler_context)

    context.api.assert_edit_message_called(
        update, expected_view(user_with_settings, [almost_gone, still_waiting, never_stamped])
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.PAST_MEETINGS)], indirect=True)
async def test_show_past_meetings_keeps_one_order_for_meetings_facing_the_same_day(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    """Without a tie-breaker the page a meeting sits on would move between two identical screens."""
    expired = expired_days_ago(30)
    first = create_meetup(id=10, active=False, expiration_time=expired)
    second = create_meetup(id=11, active=False, expiration_time=expired)
    user_with_settings.meetups = [second, first]
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_PAST_MEETINGS_CALLBACK, handler_context=handler_context)

    context.api.assert_edit_message_called(update, expected_view(user_with_settings, [first, second]))


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.SHOW_PAST_MEETING_PAGE.with_id(1))], indirect=True)
async def test_show_past_meetings_page_navigation_shows_correct_view(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    past_meetings = [create_meetup(id=meeting_id, active=False) for meeting_id in range(10, 14)]
    user_with_settings.meetups = past_meetings
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_PAST_MEETING_PAGE_CALLBACK, handler_context=handler_context)

    context.api.assert_edit_message_called(update, expected_view(user_with_settings, past_meetings))


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.SHOW_PAST_MEETING_PAGE.with_id(2))], indirect=True)
async def test_show_past_meetings_embeds_current_page_in_the_open_chips(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    """Each open chip must encode the page it was shown on so the card can return to it."""
    user_with_settings.meetups = [create_meetup(id=meeting_id, active=False) for meeting_id in range(10, 18)]
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_PAST_MEETING_PAGE_CALLBACK, handler_context=handler_context)

    view = context.api.call_args("edit_message").kwargs["view"]
    assert open_callbacks(view) == [f"show;past_meeting:{meeting_id};page:2" for meeting_id in range(15, 18)]


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.SHOW_PAST_MEETING_PAGE.with_id(9))], indirect=True)
async def test_show_past_meetings_clamps_out_of_range_page(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    """A stale page beyond the last one (e.g. after deleting its only item) clamps to the last page
    instead of raising."""
    user_with_settings.meetups = [create_meetup(id=meeting_id, active=False) for meeting_id in range(10, 14)]
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_PAST_MEETING_PAGE_CALLBACK, handler_context=handler_context)

    view = context.api.call_args("edit_message").kwargs["view"]
    assert all(callback.endswith(";page:1") for callback in open_callbacks(view))


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.PAST_MEETINGS)], indirect=True)
async def test_show_past_meetings_excludes_active_meetings(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    """Active meetings must not appear in the past meetings list."""
    past = create_meetup(id=10, active=False)
    user_with_settings.meetups = [past, create_meetup(id=11, active=True)]
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_PAST_MEETINGS_CALLBACK, handler_context=handler_context)

    context.api.assert_edit_message_called(update, expected_view(user_with_settings, [past]))


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.PAST_MEETINGS)], indirect=True)
async def test_show_past_meetings_without_past_meetings_answers_an_alert(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    user_with_settings.meetups = [create_meetup(id=10, active=True)]
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_PAST_MEETINGS_CALLBACK, handler_context=handler_context)

    context.api.assert_answer_callback_query_called(
        update,
        text=MeetingListMessages.PAST_EMPTY_ALERT.text(lang=user_with_settings.lang),
        show_alert=True,
    )
    context.api.assert_edit_message_not_called()


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.SHOW_PAST_MEETING_PAGE.with_id(1))], indirect=True)
async def test_show_past_meetings_page_answers_an_alert_when_the_list_emptied(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    """A page button on a screen whose list has since emptied reaches the same alert as the chip."""
    user_with_settings.meetups = []
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(MainMenuHandlerId.SHOW_PAST_MEETING_PAGE_CALLBACK, handler_context=handler_context)

    context.api.assert_answer_callback_query_called(
        update,
        text=MeetingListMessages.PAST_EMPTY_ALERT.text(lang=user_with_settings.lang),
        show_alert=True,
    )
    context.api.assert_edit_message_not_called()


def deleted_meetup_ids(session: MockDbSession) -> set[int]:
    """The meetup ids the handler's DELETE removed, read back from the SQL it executed."""
    ids: set[int] = set()
    for query in session.queries_executed:
        if (found := re.search(r"DELETE FROM meetups WHERE meetups\.id IN \(([^)]*)\)", query)) is not None:
            ids.update(int(part) for part in found.group(1).split(", ") if part.isdigit())
    return ids


def success_view(user: User, count: int) -> MitupView:
    return MitupView(
        meeting_views.list_heading(ButtonMessages.PAST_MEETINGS, user.lang),
        [[ButtonConfig(text=ButtonMessages.MAIN_MENU.back(lang=user.lang), callback_data=cb.MAIN_MENU)]],
    ).with_context(MeetingLifecycleMessages.DELETE_ALL_SUCCESS.rich(lang=user.lang, count=count))


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.DELETE_ALL_PAST_MEETINGS)], indirect=True)
async def test_delete_all_past_meetings_prompts_with_the_count_it_reads_from_the_database(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_objects_with_statement(past_meetings_count_statement(user_with_settings), (83,))

    context, _ = await call_handler(
        MainMenuHandlerId.DELETE_ALL_PAST_MEETINGS_CALLBACK, handler_context=handler_context
    )

    context.api.assert_edit_message_called(
        update,
        factory.confirmation_view(
            RenderContext(lang=user_with_settings.lang),
            message=MeetingLifecycleMessages.DELETE_ALL_CONFIRMATION.rich(lang=user_with_settings.lang, count=83),
            confirm_callback_data=cb.CONFIRM_DELETE_ALL_PAST_MEETINGS,
            decline_callback_data=cb.DECLINE_DELETE_ALL_PAST_MEETINGS,
            confirm_label=ButtonMessages.CONFIRM_DELETE_ALL_PAST_MEETINGS,
            decline_label=ButtonMessages.DECLINE_DELETE_ALL_PAST_MEETINGS,
        ),
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.CONFIRM_DELETE_ALL_PAST_MEETINGS)], indirect=True)
async def test_confirm_delete_all_past_meetings_deletes_every_past_meeting_and_leaves_the_active_ones(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    """The set is derived from the account that pressed the button, not from the button itself, and
    an active meeting is never in it."""
    past_meetings = [create_meetup(id=meeting_id, active=False) for meeting_id in range(10, 13)]
    user_with_settings.meetups = [*past_meetings, create_meetup(id=20, active=True)]
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(
        MainMenuHandlerId.CONFIRM_DELETE_ALL_PAST_MEETINGS_CALLBACK, handler_context=handler_context
    )

    assert deleted_meetup_ids(mock_session) == {10, 11, 12}
    context.api.assert_edit_message_called(update, success_view(user_with_settings, 3))
    context.api.assert_send_message_not_called()
    context.api.assert_update_meeting_messages_not_called()


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.CONFIRM_DELETE_ALL_PAST_MEETINGS)], indirect=True)
async def test_confirm_delete_all_past_meetings_purges_the_users_invited_into_them(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    past = create_meetup(id=10, active=False)
    invited = create_user(id=3, tg_user_id=-1, first_name="Outside")
    create_joined_link(user=invited, meetup=past, id=1)
    user_with_settings.meetups = [past]
    mock_session.add_object(user_with_settings, "tg_user_id")

    await call_handler(MainMenuHandlerId.CONFIRM_DELETE_ALL_PAST_MEETINGS_CALLBACK, handler_context=handler_context)

    assert "DELETE FROM users WHERE users.id IN (3)" in mock_session.queries_executed


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.CONFIRM_DELETE_ALL_PAST_MEETINGS)], indirect=True)
async def test_confirm_delete_all_past_meetings_deletes_nothing_when_the_list_emptied_meanwhile(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    """A confirmation rendered against 83 meetings still deletes only what the account owns now."""
    user_with_settings.meetups = [create_meetup(id=20, active=True)]
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(
        MainMenuHandlerId.CONFIRM_DELETE_ALL_PAST_MEETINGS_CALLBACK, handler_context=handler_context
    )

    assert deleted_meetup_ids(mock_session) == set()
    context.api.assert_edit_message_called(update, success_view(user_with_settings, 0))


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.DECLINE_DELETE_ALL_PAST_MEETINGS)], indirect=True)
async def test_decline_delete_all_past_meetings_returns_to_the_past_meetings_screen(
    mock_session: MockDbSession,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
):
    past_meetings = [create_meetup(id=meeting_id, active=False) for meeting_id in range(10, 13)]
    user_with_settings.meetups = past_meetings
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(
        MainMenuHandlerId.DECLINE_DELETE_ALL_PAST_MEETINGS_CALLBACK, handler_context=handler_context
    )

    assert deleted_meetup_ids(mock_session) == set()
    context.api.assert_edit_message_called(update, expected_view(user_with_settings, past_meetings))
