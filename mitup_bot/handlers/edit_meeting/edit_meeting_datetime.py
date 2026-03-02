import datetime as dt
import logging
from re import Match
from typing import cast

from sqlmodel import Session
from telegram import Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import guards
from mitup_bot.custom_context import ContextId
from mitup_bot.db import with_async_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.models import Meetup
from mitup_bot.monitoring import MetricKey
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.entities import render
from mitup_bot.utils.messages import ButtonMessages, MeetingMessages
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import ButtonConfig, MitupView, factory

from .enums import ConversationMeetingState, EditMeetingHandlerId


def safe_anchor_date(meeting_datetime: dt.datetime | None, user_now: dt.datetime) -> dt.date:
    """
    Returns the anchor date for the calendar view. If the meeting has a datetime set, and it is in the future,
    return the date of the meeting. Otherwise, return the current date.
    """
    if meeting_datetime:
        meeting_date = dt.date(meeting_datetime.year, meeting_datetime.month, meeting_datetime.day)
        return meeting_date if meeting_date >= user_now.date() else user_now.date()
    return user_now.date()


@HandlersRegistry.register_callback_query(EditMeetingHandlerId.DATE_CALLBACK, callback_data=cb.EDIT_MEETING_DATE)
@with_async_session
async def callback_edit_meeting_date(session: Session, update: Update, context: TMitupContext):
    """
    This callback is called whenever we either tap on the edit date button in the meeting edit view or when we
    navigate through the calendar view.
    """
    logging.debug("Enter into callback_edit_meeting_date")
    assert context.matches is not None

    callback_data = guards.valid_date_callback_data(
        cb.EDIT_MEETING_DATE.parse(context.match), EditMeetingHandlerId.DATE_CALLBACK
    )

    user = guards.current_user(update, session)
    if (
        meeting := await guards.meeting_accessible(session, user, callback_data.id, "Edit date", update, context)
    ) is None:
        return

    # If the meeting already has a date selected, and is either in the current month or the future
    # lets mark that date as anchor, otherwise take today.
    # Need cast because pyright cannot infer the type of models properly
    now_in_user_timezone = meeting.owner.now_in_tz()
    today_in_user_timezone = now_in_user_timezone.date()
    anchor_date = safe_anchor_date(meeting.datetime, now_in_user_timezone)

    # If the date we want to navigate to is in the past, show the present month
    current_date = callback_data.date if today_in_user_timezone <= callback_data.date else today_in_user_timezone

    meeting_date_in_tz = meeting.owner.datetime_in_tz(meeting.datetime) if meeting.datetime else None
    logging.info(
        "Calendar view: "
        f"Anchor date: {anchor_date}, Current date: {current_date}, "
        f"Meeting date: {meeting_date_in_tz}, Now tz: {now_in_user_timezone} "
        f"Callback date: {callback_data.date}"
    )

    await context.api.edit_message(
        update=update,
        view=factory.edit_meeting_date_view(
            lang=user.lang,
            meeting_id=callback_data.id,
            anchor_date=anchor_date,
            current_date=current_date,
            new=meeting.datetime is None,
        ),
    )


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DELETE_DATE_TIME_CALLBACK, callback_data=cb.DELETE_MEETING_DATE
)
@with_async_session
async def callback_delete_date_time(session: Session, update: Update, context: TMitupContext):
    """
    This callback is called when the user wants to delete the date and time of the meeting.
    It shows a confirmation dialog instead of deleting immediately.
    """
    logging.debug("Enter into callback_delete_date_time")

    callback_data = guards.valid_callback_data(
        cb.DELETE_MEETING_DATE.parse(context.match), EditMeetingHandlerId.DELETE_DATE_TIME_CALLBACK
    )

    user = guards.current_user(update, session)
    if (
        await guards.meeting_accessible(session, user, callback_data.id, "Delete date and time", update, context)
    ) is None:
        return

    view = factory.confirmation_view(
        lang=user.lang,
        message=MeetingMessages.DELETE_DATE_CONFIRMATION.get(lang=user.lang),
        confirm_callback_data=cb.CONFIRM_DELETE_MEETING_DATE.with_id(callback_data.id),
        decline_callback_data=cb.DECLINE_DELETE_MEETING_DATE.with_id(callback_data.id),
    )
    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.CONFIRM_DELETE_DATE_TIME_CALLBACK, callback_data=cb.CONFIRM_DELETE_MEETING_DATE
)
@with_async_session
async def callback_confirm_delete_date_time(session: Session, update: Update, context: TMitupContext):
    """
    This callback is called when the user confirms the deletion of the date and time of the meeting.
    """
    logging.debug("Enter into callback_confirm_delete_date_time")

    callback_data = guards.valid_callback_data(
        cb.CONFIRM_DELETE_MEETING_DATE.parse(context.match), EditMeetingHandlerId.CONFIRM_DELETE_DATE_TIME_CALLBACK
    )

    user = guards.current_user(update, session)
    if (
        meeting := await guards.meeting_accessible(
            session, user, callback_data.id, "Confirm delete date and time", update, context
        )
    ) is None:
        return

    meeting.datetime = None
    session.add(meeting)
    session.flush()

    view = meeting.edit_view.with_context(MeetingMessages.DATE_TIME_DELETED.get(lang=user.lang))
    await context.api.edit_message(update=update, view=view)
    await context.api.update_meeting_messages(
        session=session,
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DECLINE_DELETE_DATE_TIME_CALLBACK, callback_data=cb.DECLINE_DELETE_MEETING_DATE
)
@with_async_session
async def callback_decline_delete_date_time(session: Session, update: Update, context: TMitupContext):
    """
    This callback is called when the user declines the deletion of the date and time of the meeting.
    """
    logging.debug("Enter into callback_decline_delete_date_time")

    callback_data = guards.valid_callback_data(
        cb.DECLINE_DELETE_MEETING_DATE.parse(context.match), EditMeetingHandlerId.DECLINE_DELETE_DATE_TIME_CALLBACK
    )

    user = guards.current_user(update, session)
    if (
        meeting := await guards.meeting_accessible(
            session, user, callback_data.id, "Decline delete date and time", update, context
        )
    ) is None:
        return

    view = meeting.edit_view.with_context(MeetingMessages.DELETE_DATE_DECLINE.get(lang=user.lang))
    await context.api.edit_message(update=update, view=view)


async def handle_first_datetime_set(
    session: Session, context: TMitupContext, update: Update, meeting: Meetup, cb_date: dt.date
):
    # If the meeting already has a datetime, update the date
    meeting.datetime = dt.datetime.combine(cb_date, dt.time(23, 59, tzinfo=meeting.timezone)).astimezone(dt.UTC)
    session.add(meeting)
    session.flush()

    # Ask if the user wants to setup the time as well when setting the time for the first time.
    keyboard = [
        ButtonConfig(
            text=ButtonMessages.SET_TIME.get(lang=meeting.owner.settings.language),
            callback_data=cb.EDIT_MEETING_TIME.with_id(meeting.db_id),
        ),
        ButtonConfig(
            text=ButtonMessages.EDIT.back(lang=meeting.owner.settings.language),
            callback_data=cb.EDIT_MEETING.with_id(meeting.db_id),
        ),
    ]
    view = MitupView(
        description=MeetingMessages.NEW_DATE_SET_SUCCESS.get(
            lang=meeting.owner.settings.language,
            datetime=render(t"{meeting._datetime_display}"),
            back_edit_button=ButtonMessages.EDIT.back(lang=meeting.owner.settings.language),
            set_time_button=ButtonMessages.SET_TIME.get_text(lang=meeting.owner.settings.language),
        ),
        keyboard=[keyboard],
    )
    await context.api.edit_message(update=update, view=view)
    await context.api.update_meeting_messages(
        session=session,
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )


async def handle_datetime_update(
    session: Session, context: TMitupContext, update: Update, meeting: Meetup, cb_date: dt.date
):
    # If the meeting already has a datetime, update the date
    meeting.datetime = dt.datetime.combine(
        dt.date(cb_date.year, cb_date.month, cb_date.day),
        cast(dt.datetime, meeting.datetime).time(),
        tzinfo=dt.UTC,
    )
    session.add(meeting)
    session.flush()

    # Once the date has been updated, send back to edit the edit meeting view
    await context.api.edit_message(
        update=update,
        view=meeting.edit_view.with_context(
            MeetingMessages.DATE_UPDATE_SUCCESS.get(lang=meeting.lang, datetime=render(t"{meeting._datetime_display}"))
        ),
    )
    await context.api.update_meeting_messages(
        session=session,
        meeting=meeting,
        current_message=meeting.message_from_update(update),
        skip_current=True,
    )


@HandlersRegistry.register_callback_query(EditMeetingHandlerId.SET_DATE_CALLBACK, callback_data=cb.SET_MEETING_DATE)
@with_async_session
async def callback_set_meeting_date(session: Session, update: Update, context: TMitupContext):
    """
    The `SET_MEETING_DATE` callback is used only when a given date wants to be set
    on the meeting by tapping on a specific date on the calendar.
    """
    logging.debug("Enter into callback_set_meeting_time")

    callback_data = guards.valid_date_callback_data(
        cb.SET_MEETING_DATE.parse(context.match), EditMeetingHandlerId.SET_DATE_CALLBACK
    )

    user = guards.current_user(update, session)
    if (
        meeting := await guards.meeting_accessible(session, user, callback_data.id, "Edit date", update, context)
    ) is None:
        return

    if meeting.datetime is None:
        await handle_first_datetime_set(session, context, update, meeting, callback_data.date)
    else:
        await handle_datetime_update(session, context, update, meeting, callback_data.date)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.EDIT_TIME_CALLBACK, callback_data=cb.EDIT_MEETING_TIME, bindable=False
)
@with_async_session
async def callback_set_meeting_time(session: Session, update: Update, context: TMitupContext):
    """
    This callback method is the entry point for a conversation to set the time of the meeting. This can happen
    either by taping on the edit time button on the edit meeting view or by setting the time after setting the date.
    """
    logging.debug("Enter into callback_edit_meeting_date")

    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING_TIME.parse(context.match), EditMeetingHandlerId.EDIT_TIME_CALLBACK
    )

    user = guards.current_user(update, session)

    # Validate that the meeting is accessible before continuing with the operation
    meeting = await guards.meeting_accessible(
        session,
        user,
        callback_data.id,
        "Edit time",
        update,
        context,
    )

    if meeting is None:
        return ConversationHandler.END

    # Set the meeting id to the context
    context.store_meeting_id(ContextId.EDIT_MEETING_TIME, callback_data.id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_TIME,
        MeetingMessages.EDIT_MEETING_TIME_ON_EXIT.get(lang=user.lang),
        cb.EDIT_MEETING_CANCEL.with_id(callback_data.id),
    )

    view = MitupView(
        description=MeetingMessages.EDIT_TIME.get(lang=meeting.owner.settings.language),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get(lang=meeting.owner.settings.language),
                    callback_data=cb.EDIT_MEETING_CANCEL.with_id(callback_data.id),
                )
            ]
        ],
    )

    await context.api.edit_message(update=update, view=view)

    return ConversationMeetingState.EDIT_TIME


@HandlersRegistry.register_message(
    EditMeetingHandlerId.SET_TIME_MESSAGE,
    bindable=False,
    filters=filters.Regex(r"^(?P<hour>\d{2}):(?P<minutes>\d{2})$"),
)
@with_async_session
async def set_time_message(session: Session, update: Update, context: TMitupContext):
    """
    This is the callback that should read the time from the user and set it on the meeting. Only messages with the
    format HH:MM are accepted.
    """
    logging.debug("Enter into set_time_message")

    time_info = cast(Match, context.match).groupdict()

    # If the time is not valid (hour or minutes are not in the valid range), ask the user to send a valid time
    if not 0 <= int(time_info["hour"]) < 24 or not 0 <= int(time_info["minutes"]) < 60:
        current_user = guards.current_user(update, session)
        await context.api.send_message(
            update=update,
            view=MeetingMessages.INVALID_TIME.get(lang=current_user.settings.language),
        )
        context.emit_metric(MetricKey.ERROR.with_prefix("InvalidTime"), 1)
        return ConversationMeetingState.EDIT_TIME

    # Handle update witha valid time
    with context.meeting_id(ContextId.EDIT_MEETING_TIME) as meeting_id:
        current_user = guards.current_user(update, session)

        # The time provided by the user is in the user's timezone. Create a time that is aware of this timezone
        user_time = dt.time(int(time_info["hour"]), int(time_info["minutes"]), tzinfo=current_user.settings.tz)

        meeting = await guards.meeting_accessible(session, current_user, meeting_id, "Set time", update, context)

        if meeting is None:
            return ConversationHandler.END

        # If the meeting already has a datetime, update the time
        # otherwise, create a new datetime with the time provided by the user and date set to today
        # To combine with user time, make sure to get the date in the user's timezone
        date_to_set = current_user.datetime_in_tz(meeting.datetime or dt.datetime.now(dt.UTC)).date()
        meeting.datetime = dt.datetime.combine(date_to_set, user_time).astimezone(dt.UTC)

        session.add(meeting)
        session.flush()

        view = meeting.edit_view.with_context(
            MeetingMessages.EDIT_TIME_SUCCESS.get(
                lang=current_user.settings.language, datetime=render(t"{meeting._datetime_display}")
            )
        )

        await context.api.send_message(update=update, view=view)
        await context.api.update_meeting_messages(session=session, meeting=meeting)

        return ConversationHandler.END


async def fallback_answer(session: Session, update: Update, context: TMitupContext):
    """
    This is the fallback answer when the user sends a message that does not match the expected format for the time.
    """
    current_user = guards.current_user(update, session)

    await context.api.send_message(
        update=update,
        view=MeetingMessages.WRONG_TIME_FORMAT.get(lang=current_user.settings.language),
    )

    context.emit_metric(MetricKey.ERROR.with_prefix("WrongTimeFormat"), 1)

    return ConversationMeetingState.EDIT_TIME


@HandlersRegistry.register_message(
    EditMeetingHandlerId.WRONG_TIME_FORMAT,
    bindable=False,
    filters=filters.TEXT,
)
@with_async_session
async def wrong_message_sent_for_time(session: Session, update: Update, context: TMitupContext):
    """
    This handler is intended as a fallback on the conversation when a text not matching the expected format is sent.
    """
    logging.debug("Enter into set_time_message")

    return await fallback_answer(session, update, context)


@HandlersRegistry.register_message(
    EditMeetingHandlerId.WRONG_TIME_MESSAGE,
    bindable=False,
    filters=~filters.TEXT,
)
@with_async_session
async def wrong_message_type_sent_for_time(session: Session, update: Update, context: TMitupContext):
    """
    This handler is intended as a fallback on the conversation when a message that is not text is sent
    """
    logging.debug("Enter into set_time_message")

    return await fallback_answer(session, update, context)


HandlersRegistry.register_conversation_handler(
    EditMeetingHandlerId.EDIT_TIME_CONVERSATION,
    entry_points_handler_names=[EditMeetingHandlerId.EDIT_TIME_CALLBACK],
    states={ConversationMeetingState.EDIT_TIME: [EditMeetingHandlerId.SET_TIME_MESSAGE]},
    fallbacks=[
        EditMeetingHandlerId.WRONG_TIME_FORMAT,
        EditMeetingHandlerId.WRONG_TIME_MESSAGE,
        EditMeetingHandlerId.CANCEL,
    ],
)
