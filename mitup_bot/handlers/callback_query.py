import logging
from enum import auto
from typing import cast

from sqlmodel import Session
from telegram import Update
from telegram.ext import ConversationHandler

from mitup_bot import api, guards, views
from mitup_bot.callback_id import CallbackId
from mitup_bot.custom_context import MitupContext
from mitup_bot.db import with_async_session
from mitup_bot.exceptions import MalformedCallbackData
from mitup_bot.utils import MeetingMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import ButtonConfig, PaginatedMitupView

from .conversations_states import ConversationMeetingState
from .registry import HandlersRegistry


class CallbackQueryId(CallbackId):
    CANCEL_MEETING = auto()
    MAIN_MENU = auto()
    CREATE_MEETING = auto()
    SHOW_MEETING = auto()
    SHOW_MEETINGS = auto()


@HandlersRegistry.register_callback_query(
    CallbackQueryId.CREATE_MEETING, callback_data=cb.CREATE_MEETING, bindable=False
)
async def callback_query_create_meeting(update: Update, context: MitupContext):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    view = views.factory.create_meeting_view()

    await api.edit_message(context, update, view)

    return ConversationMeetingState.TITLE


@HandlersRegistry.register_callback_query(CallbackQueryId.SHOW_MEETING, callback_data=cb.SHOW_MEETING, bindable=True)
@with_async_session
async def callback_query_show_meeting(session: Session, update: Update, context: MitupContext):
    logging.info("Enter into callback_query_show_meeting")

    assert context.matches is not None

    callback_data = cb.SHOW_MEETING.parse(context.matches[0])

    if callback_data.id is None:
        raise MalformedCallbackData(CallbackQueryId.SHOW_MEETING, callback_data)

    meeting_id = callback_data.id
    logging.info(f"meeting id: {meeting_id}")

    user = guards.current_user(update, session)
    meeting = user.own_meeting(meeting_id)

    if meeting is not None:
        view = meeting.main_view

        await api.edit_message(context, update, view)
    else:
        logging.warning(
            "User tried opening meeting that does not belong to him. "
            f"Meeting id: {callback_data.id}, user id: {user.id}"
        )


@HandlersRegistry.register_callback_query(
    CallbackQueryId.CANCEL_MEETING, callback_data=cb.CANCEL_MEETING, bindable=False
)
async def callback_query_cancel_meeting(update: Update, context: MitupContext):
    logging.info("Enter into callback_query_cancel_meeting")

    await callback_query_main_menu(update, context)

    return ConversationHandler.END


@HandlersRegistry.register_callback_query(CallbackQueryId.MAIN_MENU, callback_data=cb.MAIN_MENU, bindable=True)
async def callback_query_main_menu(update: Update, context: MitupContext):
    logging.info("Enter into callback_query_main_menu")

    context.clean_all_user_data()

    view = views.factory.main_menu_view()

    await api.edit_message(context, update, view)


@HandlersRegistry.register_callback_query(
    CallbackQueryId.SHOW_MEETINGS, callback_data=cb.SHOW_ACTIVE_MEETING_PAGE, bindable=True
)
@with_async_session
async def callback_query_show_meetings(session: Session, update: Update, context: MitupContext):
    logging.info("Enter into callback_query_show_meetings")

    assert context.matches is not None

    callback_data = cb.SHOW_ACTIVE_MEETING_PAGE.parse(context.matches[0])

    if callback_data.id is None:
        raise MalformedCallbackData(CallbackQueryId.SHOW_MEETINGS, callback_data)

    owner_user = guards.current_user(update, session)
    user_meetings = sorted(owner_user.meetups, key=lambda meeting_id: cast(int, meeting_id.id))

    user_meetings_buttons: list[ButtonConfig] = [
        ButtonConfig(text=str(meeting.title), callback_data=cb.SHOW_MEETING.with_id(cast(int, meeting.id)))
        for meeting in user_meetings
    ]

    view = PaginatedMitupView(
        description=MeetingMessages.ACTIVE.get(),
        buttons=user_meetings_buttons,
        page_number=callback_data.id,
    )

    await api.edit_message(context, update, view)
