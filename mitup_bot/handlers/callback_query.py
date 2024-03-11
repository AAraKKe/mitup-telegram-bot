import logging
from enum import auto

from sqlmodel import Session
from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from mitup_bot.api import edit_message, send_message
from mitup_bot.db import with_async_session
from mitup_bot.models import User
from mitup_bot.utils import SettingsMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views.views import change_settings_element_view, create_meeting_view, main_menu_view, settings_view

from .conversations_states import ConversationMeetingState, ConversationSettingsState
from .registry import CallbackId, HandlersRegistry


class CallbackQueryId(CallbackId):
    SETTINGS = auto()
    SETTINGS_TIMEZONE = auto()
    CANCEL_SETTINGS = auto()
    CANCEL_MEETING = auto()
    MAIN_MENU = auto()
    CREATE_MEETING = auto()
    SHOW_MEETING = auto()


@HandlersRegistry.register_callback_query(CallbackQueryId.SETTINGS, pattern="^settings$", bindable=True)
async def callback_query_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    logging.info("Enter into callback_query_settings")

    view = settings_view()

    await edit_message(context, update, view)


@HandlersRegistry.register_callback_query(
    CallbackQueryId.SETTINGS_TIMEZONE, pattern="^global_timezone$", bindable=False
)
@with_async_session
async def callback_query_timezone(session: Session, update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    if update.effective_user is None:
        raise RuntimeError("Effective user not set")

    if update.effective_message is None:
        raise RuntimeError("Effective message not set")

    logging.info("Enter into callback_query_settings_timezone")

    if user := User.by_tg_user_id(session, update.effective_user.id):
        message = SettingsMessages.SET_TIMEZONE_SETTINGS.get(timezone=user.settings.timezone)

        view = change_settings_element_view(message)

        await send_message(context, update, view)

        return ConversationSettingsState.TIMEZONE
    else:
        raise RuntimeError("User not found")


@HandlersRegistry.register_callback_query(CallbackQueryId.CANCEL_SETTINGS, pattern="^cancel_settings$", bindable=False)
async def callback_query_cancel_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    view = settings_view()

    await send_message(context, update, view)

    return ConversationHandler.END


@HandlersRegistry.register_callback_query(CallbackQueryId.CREATE_MEETING, pattern="^new_meeting$", bindable=False)
async def callback_query_create_meeting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    view = create_meeting_view()

    await edit_message(context, update, view)

    return ConversationMeetingState.TITLE


@HandlersRegistry.register_callback_query(CallbackQueryId.SHOW_MEETING, pattern=r"^meeting_done_\d*", bindable=True)
@with_async_session
async def callback_query_show_meeting(session: Session, update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info("Enter into callback_query_show_meeting")
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    if update.effective_user is None:
        raise RuntimeError("Effective user not set")

    if update.callback_query is None:
        raise RuntimeError("Callback query data not set")

    if update.callback_query.data is None:
        raise RuntimeError("Callback query data not set")

    if callback := update.callback_query.data:
        logging.info(f"callback: {callback}")

        meeting_id = callback.split("_")[2]

        logging.info(f"meeting id: {meeting_id}")

        if user := User.by_tg_user_id(session, update.effective_user.id):
            meeting = user.own_meeting(int(meeting_id))

            if meeting is not None:
                view = meeting.main_view

                await edit_message(context, update, view)


@HandlersRegistry.register_callback_query(CallbackQueryId.CANCEL_MEETING, pattern="^cancel_meeting$", bindable=False)
async def callback_query_cancel_meeting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await callback_query_main_menu(update, context)

    return ConversationHandler.END


@HandlersRegistry.register_callback_query(CallbackQueryId.MAIN_MENU, pattern=cb.MAIN_MENU.pattern, bindable=True)
async def callback_query_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat is None:
        raise RuntimeError("Effective chat not set")

    view = main_menu_view()

    await edit_message(context, update, view)
