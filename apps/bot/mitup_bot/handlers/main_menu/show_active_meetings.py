import structlog
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
from mitup_bot.views import PaginatedMitupView, factory

from .enums import MainMenuHandlerId
from .utils import MeetingList, log_meeting_list

log = structlog.get_logger(__name__)


@HandlersRegistry.register_callback_query(
    MainMenuHandlerId.SHOW_MEETINGS_CALLBACK, callback_data=cb.SHOW_ACTIVE_MEETING_PAGE, bindable=True
)
@with_session
async def callback_query_show_meetings(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.SHOW_ACTIVE_MEETING_PAGE.parse(context.match), MainMenuHandlerId.SHOW_MEETINGS_CALLBACK
    )

    # load_collections: the list is built straight off `user.meetups`.
    user = await guards.current_user(update, session, load_collections=True)
    active_meetings = [meetup for meetup in user.meetups if meetup.active]
    user_meetings = sorted(
        (meeting for meeting in active_meetings if meeting.plain_title.strip()),
        key=lambda meeting: meeting.db_id,
    )
    page_number = PaginatedMitupView.clamp_page(callback_data.id, len(user_meetings))

    # A blank title is the only code-level reason an owner's own active meeting is missing from
    # this screen, so each drop is named rather than only counted.
    for meeting in active_meetings:
        if not meeting.plain_title.strip():
            log.warning(
                "Meeting hidden from list",
                user_id=user.db_id,
                meeting_id=meeting.db_id,
                list=MeetingList.ACTIVE.value,
                reason="blank_title",
            )

    log_meeting_list(
        user,
        MeetingList.ACTIVE,
        total=len(user.meetups),
        listed=len(user_meetings),
        requested_page=callback_data.id,
        page=page_number,
        active=len(active_meetings),
        dropped_blank_title=len(active_meetings) - len(user_meetings),
    )

    if user_meetings_buttons := [
        ButtonConfig(
            text=meeting.plain_title,
            callback_data=cb.SHOW_MEETING.with_page(meeting.db_id, page_number, MeetingListSource.ACTIVE),
        )
        for meeting in user_meetings
    ]:
        view = PaginatedMitupView(
            description=MeetingListMessages.ACTIVE_DESCRIPTION.get(lang=user.lang),
            buttons=user_meetings_buttons,
            page_number=page_number,
            navigation_callback_data=cb.SHOW_ACTIVE_MEETING_PAGE,
        ).with_context_menu(
            [
                [
                    ButtonConfig(
                        text=ButtonMessages.MAIN_MENU.back(lang=user.lang),
                        callback_data=cb.MAIN_MENU,
                    )
                ]
            ]
        )

    else:
        view = factory.main_menu_view(
            guards.render_context(user, update, context),
            message=MeetingListMessages.ACTIVE_EMPTY.get(
                lang=user.settings.language,
                new_meeting_button=ButtonMessages.NEW_MEETING.get(lang=user.settings.language),
            ),
        )

    await context.api.edit_message(update=update, view=view)
