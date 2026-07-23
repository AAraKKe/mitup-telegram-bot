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


@HandlersRegistry.register_callback_query(
    MainMenuHandlerId.SHOW_JOINED_MEETINGS_CALLBACK, callback_data=cb.SHOW_JOINED_MEETINGS_PAGE, bindable=True
)
@with_session
async def callback_query_show_joined_meetings(session: AsyncSession, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.SHOW_JOINED_MEETINGS_PAGE.parse(context.match), MainMenuHandlerId.SHOW_JOINED_MEETINGS_CALLBACK
    )

    user = await guards.current_user(update, session)
    joined_meetings = sorted(
        (link.meetup for link in user.joined_links if link.meetup.active),
        key=lambda meetup: meetup.db_id,
    )
    page_number = PaginatedMitupView.clamp_page(callback_data.id, len(joined_meetings))

    if user_meetings_buttons := [
        ButtonConfig(
            text=meeting.plain_title,
            callback_data=cb.SHOW_MEETING.with_page(meeting.db_id, page_number, MeetingListSource.JOINED),
        )
        for meeting in joined_meetings
    ]:
        view = PaginatedMitupView(
            description=MeetingListMessages.JOINED_DESCRIPTION.get(lang=user.lang),
            buttons=user_meetings_buttons,
            page_number=page_number,
            navigation_callback_data=cb.SHOW_JOINED_MEETINGS_PAGE,
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
            message=MeetingListMessages.JOINED_EMPTY.get(lang=user.lang),
        )

    await context.api.edit_message(update=update, view=view)
