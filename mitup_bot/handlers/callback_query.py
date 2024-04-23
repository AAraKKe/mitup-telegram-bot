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
from mitup_bot.utils import ButtonMessages, MeetingMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import ButtonConfig, MitupView, PaginatedMitupView

from .conversations_states import ConversationMeetingState
from .registry import HandlersRegistry


class CallbackQueryId(CallbackId):
    CANCEL_MEETING = auto()
    MAIN_MENU = auto()
    CREATE_MEETING = auto()
    SHOW_MEETING = auto()
    SHOW_MEETINGS = auto()
    DELETE_MEETING = auto()
    CONFIRM_DELETE_MEETING = auto()
    DECLINE_DELETE_MEETING = auto()


@HandlersRegistry.register_callback_query(
    CallbackQueryId.CREATE_MEETING, callback_data=cb.CREATE_MEETING, bindable=False
)
async def callback_query_create_meeting(update: Update, context: MitupContext):
    logging.info("Enter into callback_query_create_meeting")

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

    user = guards.current_user(update, session)
    meeting = await guards.meeting_accessible(
        session,
        user,
        meeting_id,
        "Show meeting",
        update,
        context,
        custom_keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.ACTIVE_MEETINGS.get(), callback_data=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(1)
                ),
            ]
        ],
    )
    if meeting is None:
        return

    await api.edit_message(context, update, meeting.main_view)


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

    if user_meetings_buttons := [
        ButtonConfig(
            text=str(meeting.title),
            callback_data=cb.SHOW_MEETING.with_id(cast(int, meeting.id)),
        )
        for meeting in user_meetings
    ]:
        view = PaginatedMitupView(
            description=MeetingMessages.ACTIVE.get(),
            buttons=user_meetings_buttons,
            page_number=callback_data.id,
        )

    else:
        view = MitupView(
            description=MeetingMessages.NO_MEETINGS_FOUND.get(),
            keyboard=[[ButtonConfig(text=ButtonMessages.MAIN_MENU.get(), callback_data=cb.MAIN_MENU)]],
        )

    await api.edit_message(context, update, view)


@HandlersRegistry.register_callback_query(
    CallbackQueryId.DELETE_MEETING, callback_data=cb.DELETE_MEETING, bindable=True
)
@with_async_session
async def callback_query_delete_meeting(session: Session, update: Update, context: MitupContext):
    logging.info("Enter into callback_query_delete_meeting")

    assert context.matches is not None

    user = guards.current_user(update, session)
    meeting_id = cb.DELETE_MEETING.parse(context.matches[0]).id

    if meeting_id is None:
        raise MalformedCallbackData(CallbackQueryId.DELETE_MEETING, cb.DELETE_MEETING)

    meeting = await guards.meeting_accessible(
        session,
        user,
        meeting_id,
        "Delete meeting",
        update,
        context,
    )
    if meeting is None:
        return

    await api.send_message(
        context,
        update,
        MitupView(
            description=MeetingMessages.DELETE_MEETING.get(),
            keyboard=[
                [
                    ButtonConfig(
                        text=ButtonMessages.CONFIRM.get(),
                        callback_data=cb.CONFIRM_DELETE_MEETING.with_id(meeting_id),
                    ),
                    ButtonConfig(
                        text=ButtonMessages.DECLINE.get(),
                        callback_data=cb.DECLINE_DELETE_MEETING.with_id(meeting_id),
                    ),
                ]
            ],
        ),
    )


@HandlersRegistry.register_callback_query(
    CallbackQueryId.CONFIRM_DELETE_MEETING, callback_data=cb.CONFIRM_DELETE_MEETING, bindable=True
)
@with_async_session
async def callback_query_confirm_delete_meeting(session: Session, update: Update, context: MitupContext):
    logging.info("Enter into callback_query_confirm_delete_meeting")

    assert context.matches is not None

    user = guards.current_user(update, session)
    meeting_id = cb.CONFIRM_DELETE_MEETING.parse(context.matches[0]).id

    if meeting_id is None:
        raise MalformedCallbackData(CallbackQueryId.CONFIRM_DELETE_MEETING, cb.CONFIRM_DELETE_MEETING)

    meeting = await guards.meeting_accessible(
        session,
        user,
        meeting_id,
        "Confirm delete meeting",
        update,
        context,
    )
    if meeting is None:
        return

    session.delete(meeting)

    view = MitupView(
        description=MeetingMessages.DELETE_MEETING_SUCCESS.get(),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.MAIN_MENU.get(),
                    callback_data=cb.MAIN_MENU,
                )
            ]
        ],
    )
    await api.send_message(context, update, view)


@HandlersRegistry.register_callback_query(
    CallbackQueryId.DECLINE_DELETE_MEETING, callback_data=cb.DECLINE_DELETE_MEETING, bindable=True
)
@with_async_session
async def callback_query_decline_delete_meeting(session: Session, update: Update, context: MitupContext):
    logging.info("Enter into callback_query_decline_delete_meeting")

    assert context.matches is not None

    user = guards.current_user(update, session)
    meeting_id = cb.DECLINE_DELETE_MEETING.parse(context.matches[0]).id

    if meeting_id is None:
        raise MalformedCallbackData(CallbackQueryId.CONFIRM_DELETE_MEETING, cb.DECLINE_DELETE_MEETING)

    meeting = await guards.meeting_accessible(
        session,
        user,
        meeting_id,
        "Decline delete meeting",
        update,
        context,
    )
    if meeting is None:
        return

    await api.edit_message(
        context, update, meeting.main_view.with_context(MeetingMessages.DELETE_MEETING_DECLINE.get())
    )
