import logging

from sqlmodel import Session
from telegram import Update

from mitup_bot import api, guards
from mitup_bot.custom_context import MitupContext
from mitup_bot.db import with_async_session
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.utils import ButtonMessages, MeetingMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import ButtonConfig, PaginatedMitupView, factory

from .enums import MainMenuHandlerId


@HandlersRegistry.register_callback_query(
    MainMenuHandlerId.SHOW_MEETINGS_CALLBACK, callback_data=cb.SHOW_ACTIVE_MEETING_PAGE, bindable=True
)
@with_async_session
async def callback_query_show_meetings(session: Session, update: Update, context: MitupContext):
    logging.debug("Enter into callback_query_show_meetings")

    callback_data = guards.valid_callback_data(
        cb.SHOW_ACTIVE_MEETING_PAGE.parse(context.match), MainMenuHandlerId.SHOW_MEETINGS_CALLBACK
    )

    user = guards.current_user(update, session)
    user_meetings = sorted(user.meetups, key=lambda meeting_id: meeting_id.db_id)

    if user_meetings_buttons := [
        ButtonConfig(
            text=str(meeting.title),
            callback_data=cb.SHOW_MEETING.with_id(meeting.db_id),
        )
        for meeting in user_meetings
    ]:
        view = PaginatedMitupView(
            description=MeetingMessages.ACTIVE_MEETINGS_PAGE.get(lang=user.lang),
            buttons=user_meetings_buttons,
            page_number=callback_data.id,
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
            lang=user.lang,
            message=MeetingMessages.NO_MEETINGS_FOUND.get(
                lang=user.settings.language,
                new_meeting_button=ButtonMessages.NEW_MEETING.get(lang=user.settings.language),
            ),
        )

    await api.edit_message(context=context, update=update, view=view)
