import datetime as dt

import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.deletion import purge_meetups
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import Meetup, User
from mitup_bot.models.users import past_meetings_count_statement
from mitup_bot.utils import ButtonMessages, MeetingLifecycleMessages, MeetingListMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import MitupView, PaginatedMitupView, factory
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
        await context.api.answer_callback_query(
            update=update, text=MeetingListMessages.PAST_EMPTY_ALERT.text(lang=user.lang), show_alert=True
        )
        return

    view = PaginatedMitupView(
        message=meeting_views.list_heading(ButtonMessages.PAST_MEETINGS, user.lang),
        sections=[
            meeting_views.meeting_list_section(
                meeting,
                cb.SHOW_PAST_MEETING.with_page(meeting.db_id, page_number),
                user.lang,
                delete_callback=cb.DELETE_PAST_MEETING.with_page(meeting.db_id, page_number),
                with_deletion_notice=True,
            )
            for meeting in past_meetings
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

    await context.api.edit_message(update=update, view=view)


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
    user = await guards.current_user(update, session)
    count = (await session.exec(past_meetings_count_statement(user))).first() or 0

    log.info("Meeting destructive action prompted", user_id=user.db_id, action=DELETE_ALL_PAST_MEETINGS_ACTION)

    await context.api.edit_message(
        update=update,
        view=factory.confirmation_view(
            guards.render_context(user, update, context),
            message=MeetingLifecycleMessages.DELETE_ALL_CONFIRMATION.rich(lang=user.lang, count=count),
            confirm_callback_data=cb.CONFIRM_DELETE_ALL_PAST_MEETINGS,
            decline_callback_data=cb.DECLINE_DELETE_ALL_PAST_MEETINGS,
            confirm_label=ButtonMessages.CONFIRM_DELETE_ALL_PAST_MEETINGS,
            decline_label=ButtonMessages.DECLINE_DELETE_ALL_PAST_MEETINGS,
        ),
    )


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

    view = MitupView(
        meeting_views.list_heading(ButtonMessages.PAST_MEETINGS, user.lang),
        [[ButtonConfig(text=ButtonMessages.MAIN_MENU.back(lang=user.lang), callback_data=cb.MAIN_MENU)]],
    ).with_context(MeetingLifecycleMessages.DELETE_ALL_SUCCESS.rich(lang=user.lang, count=len(past_meetings)))
    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    MainMenuHandlerId.DECLINE_DELETE_ALL_PAST_MEETINGS_CALLBACK,
    callback_data=cb.DECLINE_DELETE_ALL_PAST_MEETINGS,
    bindable=True,
)
@with_session
async def callback_query_decline_delete_all_past_meetings(
    session: AsyncSession, update: Update, context: TMitupContext
):
    # Each meeting renders as a card section, which reads its owner and its joined links.
    user = await guards.current_user(update, session, load_collections=True, load_participants=True)

    log.info("Meeting destructive action declined", user_id=user.db_id, action=DELETE_ALL_PAST_MEETINGS_ACTION)

    await show_past_meetings_page(user, 1, update, context)
