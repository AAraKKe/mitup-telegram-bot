import datetime as dt

import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.api_wrapper import replace_message
from mitup_bot.db import with_session
from mitup_bot.deletion import purge_meetups
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import Meetup, User
from mitup_bot.utils import ButtonMessages, MeetingLifecycleMessages, MeetingListMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.rich_message import RichContent
from mitup_bot.views import PaginatedMitupView, factory
from mitup_bot.views import meeting as meeting_views

from .enums import MainMenuHandlerId
from .utils import MEETINGS_PER_PAGE, MeetingList, log_meeting_list

log = structlog.get_logger(__name__)

# The `action` facet both destructive-confirmation lines of the bulk delete carry.
DELETE_ALL_PAST_MEETINGS_ACTION = "delete_all_past_meetings"

# Undated meetings sort after every meeting with a deletion due time.
UNDATED_DELETION = dt.datetime.max.replace(tzinfo=dt.UTC)


def past_meetings_of(user: User) -> list[Meetup]:
    return sorted(
        [meeting for meeting in user.meetups if not meeting.active],
        key=lambda meeting: (meeting.deletion_due_time or UNDATED_DELETION, meeting.db_id),
    )


def past_list_sections(
    meetings: list[Meetup], lang: str, page_number: int, *, inert: bool = False
) -> list[RichContent]:
    """The rows of the past-meetings list. Inert rows carry no keys: on the screen asking whether
    to delete them all, the only choice is the one in the keyboard."""
    return [
        meeting_views.meeting_list_section(
            meeting,
            None if inert else cb.SHOW_PAST_MEETING.with_page(meeting.db_id, page_number),
            lang,
            delete_callback=None if inert else cb.DELETE_PAST_MEETING.with_page(meeting.db_id, page_number),
            with_deletion_notice=True,
        )
        for meeting in meetings
    ]


def past_meetings_view(user: User, meetings: list[Meetup], page_number: int) -> PaginatedMitupView:
    return PaginatedMitupView(
        message=meeting_views.list_heading(ButtonMessages.PAST_MEETINGS, user.lang),
        sections=past_list_sections(meetings, user.lang, page_number),
        page_size=MEETINGS_PER_PAGE,
        page_number=page_number,
        navigation_callback_data=cb.SHOW_PAST_MEETING_PAGE,
    ).with_context_menu(
        [
            [
                ButtonConfig(
                    text=ButtonMessages.DELETE_ALL_PAST_MEETINGS.text(lang=user.lang),
                    callback_data=cb.DELETE_ALL_PAST_MEETINGS.with_id(page_number),
                    style="danger",
                )
            ],
            [ButtonConfig(text=ButtonMessages.MAIN_MENU.back(lang=user.lang), callback_data=cb.MAIN_MENU)],
        ]
    )


def delete_all_prompt_view(user: User, meetings: list[Meetup], page_number: int) -> PaginatedMitupView:
    """The past list as its own confirmation, so the whole screen is what dissolves on the tap."""
    return (
        PaginatedMitupView(
            message=meeting_views.list_heading(ButtonMessages.PAST_MEETINGS, user.lang),
            sections=past_list_sections(meetings, user.lang, page_number, inert=True),
            page_size=MEETINGS_PER_PAGE,
            page_number=page_number,
            navigation_callback_data=cb.DELETE_ALL_PAST_MEETINGS,
        )
        .with_footnote(MeetingLifecycleMessages.DELETE_ALL_CONFIRMATION.rich(lang=user.lang, count=len(meetings)))
        .with_context_menu(
            [
                factory.confirmation_row(
                    user.lang,
                    confirm_callback_data=cb.CONFIRM_DELETE_ALL_PAST_MEETINGS,
                    decline_callback_data=cb.DECLINE_DELETE_ALL_PAST_MEETINGS.with_id(page_number),
                    confirm_label=ButtonMessages.CONFIRM_DELETE_ALL_PAST_MEETINGS,
                    decline_label=ButtonMessages.DECLINE_DELETE_ALL_PAST_MEETINGS,
                    confirm_style="danger",
                    decline_style=None,
                )
            ]
        )
    )


async def answer_past_list_is_empty(user: User, update: Update, context: TMitupContext):
    await context.api.answer_callback_query(
        update=update, text=MeetingListMessages.PAST_EMPTY_ALERT.text(lang=user.lang), show_alert=True
    )


async def show_past_meetings_page(user: User, requested_page: int, update: Update, context: TMitupContext):
    past_meetings = past_meetings_of(user)
    page_number = PaginatedMitupView.clamp_page(requested_page, len(past_meetings), MEETINGS_PER_PAGE)

    log_meeting_list(
        user,
        MeetingList.PAST,
        total=len(user.meetups),
        listed=len(past_meetings),
        requested_page=requested_page,
        page=page_number,
        dropped_active=len(user.meetups) - len(past_meetings),
    )

    if not past_meetings:
        await answer_past_list_is_empty(user, update, context)
        return

    await context.api.edit_message(update=update, view=past_meetings_view(user, past_meetings, page_number))


@HandlersRegistry.register_callback_query(
    MainMenuHandlerId.SHOW_PAST_MEETINGS_CALLBACK, callback_data=cb.PAST_MEETINGS, bindable=True
)
@with_session
async def callback_query_show_past_meetings(session: AsyncSession, update: Update, context: TMitupContext):
    # Each meeting renders as a card section, which reads its owner and its joined links.
    user = await guards.current_user(update, session, load_collections=True, load_participants=True)
    await show_past_meetings_page(user, 1, update, context)


@HandlersRegistry.register_callback_query(
    MainMenuHandlerId.SHOW_PAST_MEETING_PAGE_CALLBACK, callback_data=cb.SHOW_PAST_MEETING_PAGE, bindable=True
)
@with_session
async def callback_query_show_past_meeting_page(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.SHOW_PAST_MEETING_PAGE.parse(context.match), MainMenuHandlerId.SHOW_PAST_MEETING_PAGE_CALLBACK
    )
    # Each meeting renders as a card section, which reads its owner and its joined links.
    user = await guards.current_user(update, session, load_collections=True, load_participants=True)
    await show_past_meetings_page(user, callback_data.id, update, context)


@HandlersRegistry.register_callback_query(
    MainMenuHandlerId.DELETE_ALL_PAST_MEETINGS_CALLBACK, callback_data=cb.DELETE_ALL_PAST_MEETINGS, bindable=True
)
@with_session
async def callback_query_delete_all_past_meetings(session: AsyncSession, update: Update, context: TMitupContext):
    # A chip on a list already sitting in a chat may carry no page.
    callback_data = cb.DELETE_ALL_PAST_MEETINGS.parse(context.match)
    # The prompt is the list itself, so every meeting renders as a card section here too.
    user = await guards.current_user(update, session, load_collections=True, load_participants=True)
    past_meetings = past_meetings_of(user)

    log.info("Meeting destructive action prompted", user_id=user.db_id, action=DELETE_ALL_PAST_MEETINGS_ACTION)

    if not past_meetings:
        await answer_past_list_is_empty(user, update, context)
        return

    page_number = PaginatedMitupView.clamp_page(callback_data.id or 1, len(past_meetings), MEETINGS_PER_PAGE)
    await context.api.edit_message(update=update, view=delete_all_prompt_view(user, past_meetings, page_number))


@HandlersRegistry.register_callback_query(
    MainMenuHandlerId.CONFIRM_DELETE_ALL_PAST_MEETINGS_CALLBACK,
    callback_data=cb.CONFIRM_DELETE_ALL_PAST_MEETINGS,
    bindable=True,
)
@with_session(write=True)
async def callback_query_confirm_delete_all_past_meetings(
    session: AsyncSession, update: Update, context: TMitupContext
):
    # The set is re-derived from the account that pressed the button, and the purge reads each
    # meeting's joined links to destroy the users invited into it.
    user = await guards.current_user(update, session, load_collections=True, load_participants=True)
    past_meetings = past_meetings_of(user)

    # The rows go for good, so this line is the only record of which meetings the tap took.
    log.info(
        "Past meetings purged",
        user_id=user.db_id,
        count=len(past_meetings),
        meeting_ids=[meeting.db_id for meeting in past_meetings],
        reason="owner_confirmed_all",
    )

    await purge_meetups(session, past_meetings)

    view = factory.main_menu_view(
        guards.render_context(user, update, context),
        message=MeetingLifecycleMessages.DELETE_ALL_SUCCESS.rich(lang=user.lang, count=len(past_meetings)),
        counts=await user.meeting_counts(session),
    )
    await replace_message(context.api, update, view)


@HandlersRegistry.register_callback_query(
    MainMenuHandlerId.DECLINE_DELETE_ALL_PAST_MEETINGS_CALLBACK,
    callback_data=cb.DECLINE_DELETE_ALL_PAST_MEETINGS,
    bindable=True,
)
@with_session
async def callback_query_decline_delete_all_past_meetings(
    session: AsyncSession, update: Update, context: TMitupContext
):
    # A key on a prompt already sitting in a chat may carry no page.
    callback_data = cb.DECLINE_DELETE_ALL_PAST_MEETINGS.parse(context.match)
    # Each meeting renders as a card section, which reads its owner and its joined links.
    user = await guards.current_user(update, session, load_collections=True, load_participants=True)

    log.info("Meeting destructive action declined", user_id=user.db_id, action=DELETE_ALL_PAST_MEETINGS_ACTION)

    await show_past_meetings_page(user, callback_data.id or 1, update, context)
