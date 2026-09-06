from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.callback_data import MeetingListSource
from mitup_bot.db import with_session
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.utils import ButtonMessages, MeetingListMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import PaginatedMitupView
from mitup_bot.views import meeting as meeting_views

from .enums import MainMenuHandlerId
from .utils import MEETINGS_PER_PAGE, MeetingList, log_meeting_list


@HandlersRegistry.register_callback_query(
    MainMenuHandlerId.SHOW_JOINED_MEETINGS_CALLBACK, callback_data=cb.SHOW_JOINED_MEETINGS_PAGE, bindable=True
)
@with_session
async def callback_query_show_joined_meetings(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.SHOW_JOINED_MEETINGS_PAGE.parse(context.match), MainMenuHandlerId.SHOW_JOINED_MEETINGS_CALLBACK
    )

    # load_collections: the list is built straight off `user.joined_links` and each link's meetup;
    # load_participants: each meeting is rendered as a card section, which reads its owner and its
    # joined links.
    user = await guards.current_user(update, session, load_collections=True, load_participants=True)
    joined_meetings = sorted(
        (link.meetup for link in user.joined_links if link.meetup.active),
        key=lambda meetup: meetup.db_id,
    )
    page_number = PaginatedMitupView.clamp_page(callback_data.id, len(joined_meetings), MEETINGS_PER_PAGE)

    log_meeting_list(
        user,
        MeetingList.JOINED,
        total=len(user.joined_links),
        listed=len(joined_meetings),
        requested_page=callback_data.id,
        page=page_number,
        dropped_inactive=len(user.joined_links) - len(joined_meetings),
    )

    if not joined_meetings:
        await context.api.answer_callback_query(
            update=update, text=MeetingListMessages.JOINED_EMPTY_ALERT.text(lang=user.lang), show_alert=True
        )
        return

    view = PaginatedMitupView(
        message=meeting_views.list_heading(ButtonMessages.JOINED_MEETINGS, user.lang),
        sections=[
            meeting_views.meeting_list_section(
                meeting,
                cb.SHOW_MEETING.with_page(meeting.db_id, page_number, MeetingListSource.JOINED),
                user.lang,
            )
            for meeting in joined_meetings
        ],
        page_size=MEETINGS_PER_PAGE,
        page_number=page_number,
        navigation_callback_data=cb.SHOW_JOINED_MEETINGS_PAGE,
    ).with_context_menu(
        [[ButtonConfig(text=ButtonMessages.MAIN_MENU.back(lang=user.lang), callback_data=cb.MAIN_MENU)]]
    )

    await context.api.edit_message(update=update, view=view)
