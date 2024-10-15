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
from mitup_bot.monitoring import Feature
from mitup_bot.utils import ButtonMessages, MeetingMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import ButtonConfig, MitupView, PaginatedMitupView, factory

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
@with_async_session
async def callback_query_create_meeting(session: Session, update: Update, context: MitupContext):
    logging.info("Enter into callback_query_create_meeting")

    user = guards.current_user(update, session)
    view = views.factory.create_meeting_view(lang=user.lang)

    await api.edit_message(context=context, update=update, view=view)

    return ConversationMeetingState.TITLE


@HandlersRegistry.register_callback_query(CallbackQueryId.SHOW_MEETING, callback_data=cb.SHOW_MEETING, bindable=True)
@with_async_session
async def callback_query_show_meeting(session: Session, update: Update, context: MitupContext):
    logging.info("Enter into callback_query_show_meeting")

    callback_data = guards.valid_callback_data(cb.SHOW_MEETING.parse(context.match), CallbackQueryId.SHOW_MEETING)

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
                    text=ButtonMessages.ACTIVE_MEETINGS.get(lang=user.lang),
                    callback_data=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(1),
                ),
            ]
        ],
    )
    if meeting is None:
        return

    await api.edit_message(context=context, update=update, view=meeting.main_view)


@HandlersRegistry.register_callback_query(
    CallbackQueryId.CANCEL_MEETING, callback_data=cb.CANCEL_MEETING, bindable=False
)
async def callback_query_cancel_meeting(update: Update, context: MitupContext):
    logging.info("Enter into callback_query_cancel_meeting")

    await callback_query_main_menu(update, context)

    context.put_feature_metric(Feature.CREATE_MEETING, name="Cancel")
    return ConversationHandler.END


@HandlersRegistry.register_callback_query(CallbackQueryId.MAIN_MENU, callback_data=cb.MAIN_MENU, bindable=True)
@with_async_session
async def callback_query_main_menu(session: Session, update: Update, context: MitupContext):
    logging.info("Enter into callback_query_main_menu")

    context.clean_all_user_data()

    user = guards.current_user(update, session)
    view = views.factory.main_menu_view(lang=user.lang)

    await api.edit_message(context=context, update=update, view=view)


@HandlersRegistry.register_callback_query(
    CallbackQueryId.SHOW_MEETINGS, callback_data=cb.SHOW_ACTIVE_MEETING_PAGE, bindable=True
)
@with_async_session
async def callback_query_show_meetings(session: Session, update: Update, context: MitupContext):
    logging.info("Enter into callback_query_show_meetings")

    callback_data = guards.valid_callback_data(
        cb.SHOW_ACTIVE_MEETING_PAGE.parse(context.match), CallbackQueryId.SHOW_MEETINGS
    )

    user = guards.current_user(update, session)
    user_meetings = sorted(user.meetups, key=lambda meeting_id: cast(int, meeting_id.id))

    if user_meetings_buttons := [
        ButtonConfig(
            text=str(meeting.title),
            callback_data=cb.SHOW_MEETING.with_id(cast(int, meeting.id)),
        )
        for meeting in user_meetings
    ]:
        view = PaginatedMitupView(
            description=MeetingMessages.ACTIVE.get(lang=user.lang),
            buttons=user_meetings_buttons,
            page_number=callback_data.id,
        )

    else:
        view = factory.main_menu_view(
            MeetingMessages.NO_MEETINGS_FOUND.get(
                lang=user.settings.language,
                new_meeting_button=ButtonMessages.NEW_MEETING.get(lang=user.settings.language),
            )
        )

    await api.edit_message(context=context, update=update, view=view)


@HandlersRegistry.register_callback_query(
    CallbackQueryId.DELETE_MEETING, callback_data=cb.DELETE_MEETING, bindable=True
)
@with_async_session
async def callback_query_delete_meeting(session: Session, update: Update, context: MitupContext):
    logging.info("Enter into callback_query_delete_meeting")

    callback_data = guards.valid_callback_data(cb.DELETE_MEETING.parse(context.match), CallbackQueryId.DELETE_MEETING)

    user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(
        session,
        user,
        callback_data.id,
        "Delete meeting",
        update,
        context,
    )
    if meeting is None:
        return

    await api.send_message(
        context=context,
        update=update,
        view=MitupView(
            description=MeetingMessages.DELETE_MEETING.get(lang=user.lang),
            keyboard=[
                [
                    ButtonConfig(
                        text=ButtonMessages.CONFIRM.get(lang=user.lang),
                        callback_data=cb.CONFIRM_DELETE_MEETING.with_id(callback_data.id),
                    ),
                    ButtonConfig(
                        text=ButtonMessages.DECLINE.get(lang=user.lang),
                        callback_data=cb.DECLINE_DELETE_MEETING.with_id(callback_data.id),
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

    callback_data = guards.valid_callback_data(
        cb.CONFIRM_DELETE_MEETING.parse(context.match),
        CallbackQueryId.CONFIRM_DELETE_MEETING,
    )

    user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(
        session,
        user,
        callback_data.id,
        "Confirm delete meeting",
        update,
        context,
    )
    if meeting is None:
        return

    # Update messages before deleting the meeting and all its messages
    await api.update_meeting_messages(session=session, context=context, meeting=meeting, was_deleted=True)

    session.delete(meeting)

    view = MitupView(
        description=MeetingMessages.DELETE_MEETING_SUCCESS.get(lang=user.lang),
        keyboard=[
            [
                ButtonConfig(
                    text=f"{ButtonMessages.GO_BACK.get()}{ButtonMessages.MAIN_MENU.get(lang=user.lang)}",
                    callback_data=cb.MAIN_MENU,
                )
            ]
        ],
    )
    await api.send_message(context=context, update=update, view=view)


@HandlersRegistry.register_callback_query(
    CallbackQueryId.DECLINE_DELETE_MEETING, callback_data=cb.DECLINE_DELETE_MEETING, bindable=True
)
@with_async_session
async def callback_query_decline_delete_meeting(session: Session, update: Update, context: MitupContext):
    logging.info("Enter into callback_query_decline_delete_meeting")

    callback_data = guards.valid_callback_data(
        cb.DECLINE_DELETE_MEETING.parse(context.match),
        CallbackQueryId.DECLINE_DELETE_MEETING,
    )
    user = guards.current_user(update, session)

    meeting = await guards.meeting_accessible(
        session,
        user,
        callback_data.id,
        "Decline delete meeting",
        update,
        context,
    )
    if meeting is None:
        return

    await api.edit_message(
        context=context,
        update=update,
        view=meeting.main_view.with_context(MeetingMessages.DELETE_MEETING_DECLINE.get(lang=user.lang)),
    )
