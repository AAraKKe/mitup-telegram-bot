import datetime as dt

from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import User
from mitup_bot.utils import ButtonMessages, MeetingListMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import PaginatedMitupView
from mitup_bot.views import meeting as meeting_views

from .enums import MainMenuHandlerId
from .utils import MEETINGS_PER_PAGE, MeetingList, log_meeting_list

# Undated meetings sort after every meeting with a deletion due time.
UNDATED_DELETION = dt.datetime.max.replace(tzinfo=dt.UTC)


async def show_past_meetings_page(user: User, requested_page: int, update: Update, context: TMitupContext):
    past_meetings = sorted(
        [meeting for meeting in user.meetups if not meeting.active],
        key=lambda meeting: (meeting.deletion_due_time or UNDATED_DELETION, meeting.db_id),
    )
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
        [[ButtonConfig(text=ButtonMessages.MAIN_MENU.back(lang=user.lang), callback_data=cb.MAIN_MENU)]]
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
