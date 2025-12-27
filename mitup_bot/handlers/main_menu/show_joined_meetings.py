from sqlmodel import Session
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import with_async_session
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.utils import ButtonMessages, MeetingMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import ButtonConfig, PaginatedMitupView, factory

from .enums import MainMenuHandlerId


@HandlersRegistry.register_callback_query(
    MainMenuHandlerId.SHOW_JOINED_MEETINGS_CALLBACK, callback_data=cb.SHOW_JOINED_MEETINGS_PAGE, bindable=True
)
@with_async_session
async def callback_query_show_joined_meetings(session: Session, update: Update, context: TMitupContext):
    callback_data = guards.valid_callback_data(
        cb.SHOW_JOINED_MEETINGS_PAGE.parse(context.match), MainMenuHandlerId.SHOW_JOINED_MEETINGS_CALLBACK
    )

    user = guards.current_user(update, session)
    joined_meetings = sorted((link.meetup for link in user.joined_links), key=lambda meetup: meetup.db_id)

    if user_meetings_buttons := [
        ButtonConfig(
            text=str(meeting.title),
            callback_data=cb.SHOW_MEETING.with_id(meeting.db_id),
        )
        for meeting in joined_meetings
    ]:
        view = PaginatedMitupView(
            description=MeetingMessages.JOINED_MEETINGS_PAGE.get(lang=user.lang),
            buttons=user_meetings_buttons,
            page_number=callback_data.id,
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
        view = factory.main_menu_view(lang=user.lang, message=MeetingMessages.NO_JOINED_MEETINGS.get(lang=user.lang))

    await context.api.edit_message(update=update, view=view)
