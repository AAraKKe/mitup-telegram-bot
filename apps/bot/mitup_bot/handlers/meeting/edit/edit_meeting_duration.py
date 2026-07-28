import datetime as dt
from re import Match
from typing import cast

from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import MessageEntity, Update
from telegram.ext import ConversationHandler, filters

from mitup_bot import guards, limits
from mitup_bot.custom_context import ContextId
from mitup_bot.db import with_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import Meetup
from mitup_bot.monitoring import Feature, MetricKey
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.entities import EntityDateTime, FormattedText, build_datetime_link, render
from mitup_bot.utils.messages import ButtonMessages, CommonMessages, MeetingDisplayMessages, MeetingEditDurationMessages
from mitup_bot.views import MitupView, factory
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.collaborate import supporter_upsell_view

from ..utils import scheduling_horizon_rejection
from .enums import ConversationMeetingState, EditMeetingHandlerId
from .utils import DateTimeEntityFilter, cleanup_states, is_in_past, prepend_error, safe_anchor_date, to_utc

# This module manages the end-time editing sub-flow for a meeting.
#
# The start-time conversation is in edit_meeting_datetime.py. This module only handles
# end-time selection, which requires that meeting.datetime is already set.
#
# States:
#   EDIT_END_DATETIME -- always the first state
#     * Date button -> EDIT_END_DATE
#     * Time button -> EDIT_END_TIME
#     * datetime entity -> validate end > start -> save -> END
#     * Cancel -> END
#     * wrong message fallback -> stay
#
#   EDIT_END_DATE
#     * Calendar nav -> stay
#     * Date selected (no end time yet) -> EDIT_END_TIME
#     * Date selected (end time exists) -> validate -> EDIT_END_DATETIME
#     * Back -> EDIT_END_DATETIME
#
#   EDIT_END_TIME
#     * HH:MM message -> validate end > start -> save -> END
#     * datetime entity -> validate -> save -> END
#     * wrong message fallback -> stay


# --- Conversation entry ---


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DURATION_INPUT_CALLBACK, callback_data=cb.SET_MEETING_END_TIME, bindable=False
)
@with_session
async def callback_query_set_meeting_end_time(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int | None:
    meeting_id = guards.valid_callback_data(
        cb.SET_MEETING_END_TIME.parse(context.match), EditMeetingHandlerId.DURATION_INPUT_CALLBACK
    ).id
    user = await guards.current_user(update, session)

    meeting = await guards.meeting(session, user, meeting_id, "set_meeting_end_time", context)

    if meeting.datetime is None:
        await context.api.answer_callback_query(
            update,
            text=MeetingEditDurationMessages.END_STALE_ALERT.get_text(lang=user.lang),
            show_alert=True,
        )
        return ConversationHandler.END

    context.store_meeting_id(ContextId.EDIT_MEETING_DURATION, meeting_id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_DURATION,
        MeetingEditDurationMessages.ON_EXIT.get(lang=user.lang),
        cb.CANCEL_EDIT_MEETING_DURATION.with_id(meeting_id),
    )

    return await show_end_datetime_entry(context, update, meeting, user.lang)


# --- Cancel handler ---


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DURATION_CANCEL_CALLBACK, callback_data=cb.CANCEL_EDIT_MEETING_DURATION, bindable=False
)
@with_session
async def callback_query_cancel_edit_duration(session: AsyncSession, update: Update, context: TMitupContext) -> int:
    context.clean_all_user_data(reason="duration_edit_cancelled")

    meeting_id = guards.valid_callback_data(
        cb.CANCEL_EDIT_MEETING_DURATION.parse(context.match), EditMeetingHandlerId.DURATION_CANCEL_CALLBACK
    ).id
    user = await guards.current_user(update, session)

    meeting = await guards.meeting(session, user, meeting_id, "cancel_edit_meeting_duration", context)

    await context.api.edit_message(update=update, view=meeting_views.when_view(meeting))

    return ConversationHandler.END


# --- EDIT_END_DATETIME state ---


def build_end_datetime_entry_view(meeting: Meetup, lang: str, *, error: str | FormattedText | None = None) -> MitupView:
    """Build the end datetime entry view (Date / Time buttons + When back button).

    When ``error`` is given it is prepended as a leading paragraph, so a message that failed
    validation can be answered by resending this prompt with the error on top rather than a
    bare, button-less error.
    """
    assert meeting.datetime is not None
    start_entity = EntityDateTime(MeetingDisplayMessages.DATETIME_ENTITY_LABEL.get_text(), meeting.datetime, "DT")
    start_text = render(t"{start_entity}")

    datetime_link = build_datetime_link()
    if meeting.end_datetime is not None:
        end_entity = EntityDateTime(MeetingDisplayMessages.DATETIME_ENTITY_LABEL.get_text(), meeting.end_datetime, "DT")
        description = MeetingEditDurationMessages.END_EDIT_PROMPT.get(
            lang=lang, start_datetime=start_text, end_datetime=render(t"{end_entity}"), datetime_link=datetime_link
        )
    else:
        description = MeetingEditDurationMessages.END_PROMPT.get(
            lang=lang, start_datetime=start_text, datetime_link=datetime_link
        )
    if error is not None:
        description = prepend_error(description, error)

    meeting_id = meeting.db_id
    today = meeting.owner.now_in_tz().date()
    keyboard: list[list[ButtonConfig]] = [
        [
            ButtonConfig(
                text=ButtonMessages.DATE.get_text(lang=lang),
                callback_data=cb.EDIT_MEETING_END_DATE.with_id(meeting_id).with_date(today),
            ),
            ButtonConfig(
                text=ButtonMessages.TIME.get_text(lang=lang),
                callback_data=cb.EDIT_MEETING_END_TIME.with_id(meeting_id),
            ),
        ],
    ]

    return MitupView(description=description, keyboard=keyboard).with_back_button(
        ButtonMessages.WHEN, lang, cb.CANCEL_EDIT_MEETING_DURATION.with_id(meeting_id)
    )


async def show_end_datetime_entry(
    context: TMitupContext,
    update: Update,
    meeting: Meetup,
    lang: str,
) -> ConversationMeetingState:
    """Show the end datetime entry view. Transitions to EDIT_END_DATETIME state."""
    context.store_meeting_id(ContextId.EDIT_MEETING_END_DATETIME, meeting.db_id)
    context.store_on_exit(
        ContextId.EDIT_MEETING_END_DATETIME,
        MeetingEditDurationMessages.ON_EXIT.get(lang=lang),
        cb.CANCEL_EDIT_MEETING_DURATION.with_id(meeting.db_id),
    )

    await context.api.edit_message(update=update, view=build_end_datetime_entry_view(meeting, lang))

    return ConversationMeetingState.EDIT_END_DATETIME


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DURATION_END_ENTRY_CALLBACK, callback_data=cb.EDIT_MEETING_END_DATE_TIME, bindable=False
)
@with_session
async def callback_query_end_datetime_entry(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int | None:
    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING_END_DATE_TIME.parse(context.match), EditMeetingHandlerId.DURATION_END_ENTRY_CALLBACK
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, callback_data.id, "Edit end datetime", context)

    return await show_end_datetime_entry(context, update, meeting, user.lang)


def validate_end_datetime(end_dt: dt.datetime, meeting: Meetup, lang: str) -> str | None:
    """Return an error message string if end_dt is invalid, or None if valid.

    Checks the past constraint before the ordering constraint: a past end time is
    the more fundamental problem (the meeting would be auto-deactivated), so it takes
    precedence in the error shown. The one-week duration cap applies to every tier and is
    checked last, once the end is known to be a future time after the start.

    The scheduling horizon is not checked here: call sites check it separately so the rejection can
    carry the Collaborate upsell.
    """
    assert meeting.datetime is not None
    if is_in_past(end_dt, meeting):
        return MeetingEditDurationMessages.END_IN_PAST.get_text(lang=lang)
    if to_utc(end_dt) <= to_utc(meeting.datetime):
        return MeetingEditDurationMessages.END_BEFORE_START.get_text(lang=lang)
    if not limits.within_max_duration(meeting.datetime, end_dt):
        return MeetingEditDurationMessages.END_MAX_DURATION.get_text(lang=lang)
    return None


def end_beyond_horizon_view(meeting: Meetup, end_dt: dt.datetime, back_button: ButtonConfig) -> MitupView | None:
    """Collaborate upsell view when end_dt is beyond the owner's scheduling horizon, else None."""
    rejection = scheduling_horizon_rejection(meeting.owner, end_dt)
    if rejection is None:
        return None
    return supporter_upsell_view(rejection, meeting.owner.lang).with_context_menu([[back_button]])


async def reject_end_beyond_horizon(
    context: TMitupContext, update: Update, meeting: Meetup, end_dt: dt.datetime
) -> bool:
    """Return True, having informed the owner, when end_dt is beyond their scheduling horizon.

    For the calendar path: the tapped message is edited into the rejection carrying the Collaborate
    button and a button back to the calendar, so the upsell replaces the screen; the conversation
    stays in EDIT_END_DATE, where that calendar button is handled.
    """
    today = meeting.owner.now_in_tz().date()
    calendar_button = ButtonConfig(
        text=ButtonMessages.DATE.back(lang=meeting.owner.lang),
        callback_data=cb.EDIT_MEETING_END_DATE.with_id(meeting.db_id).with_date(today),
    )
    view = end_beyond_horizon_view(meeting, end_dt, calendar_button)
    if view is None:
        return False
    await context.api.edit_message(update=update, view=view)
    return True


async def reply_end_beyond_horizon(
    context: TMitupContext, update: Update, meeting: Meetup, end_dt: dt.datetime
) -> bool:
    """Return True, having informed the owner, when end_dt is beyond their scheduling horizon.

    For the message paths (a sent datetime entity or an HH:MM time): the upsell is sent as a reply
    carrying the Collaborate button and a button back to the End Date & Time entry screen. The
    caller returns EDIT_END_DATETIME, where that back button is handled.
    """
    back_button = ButtonConfig(
        text=ButtonMessages.END_DATE_TIME.back(lang=meeting.owner.lang),
        callback_data=cb.EDIT_MEETING_END_DATE_TIME.with_id(meeting.db_id),
    )
    view = end_beyond_horizon_view(meeting, end_dt, back_button)
    if view is None:
        return False
    await context.api.send_message(update=update, view=view)
    return True


async def save_end_datetime_and_finish(
    context: TMitupContext,
    update: Update,
    meeting: Meetup,
    end_dt: dt.datetime,
) -> int:
    """Shared tail of both end-datetime flows; broadcast runs post-commit via write mode."""
    meeting.end_datetime = end_dt

    response_view = meeting_views.when_view(meeting)

    await context.api.send_message(update=update, view=response_view)
    await context.api.update_meeting_messages(meeting=meeting)

    context.put_feature_metric(Feature.EDIT_MEETING, properties={"EditedField": "end_datetime"})
    cleanup_states(context)
    return ConversationHandler.END


@HandlersRegistry.register_message(
    EditMeetingHandlerId.DURATION_END_DATETIME_ENTITY_MESSAGE,
    DateTimeEntityFilter(),
    bindable=False,
)
@with_session(write=True)
async def duration_end_datetime_entity_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    message = guards.message(update)
    entities = message.entities or []
    date_entity = next(e for e in entities if e.type == MessageEntity.DATE_TIME)
    unix_time = date_entity.unix_time
    assert unix_time is not None, "date_time entity must carry unix_time"

    with context.meeting_id(ContextId.EDIT_MEETING_END_DATETIME, ensure_clean=False) as meeting_id:
        user = await guards.current_user(update, session)
        meeting = await guards.meeting(session, user, meeting_id, "Set end datetime from entity", context)

        if error := validate_end_datetime(unix_time, meeting, user.lang):
            await context.api.send_message(
                update=update, view=build_end_datetime_entry_view(meeting, user.lang, error=error)
            )
            return ConversationMeetingState.EDIT_END_DATETIME

        if await reply_end_beyond_horizon(context, update, meeting, unix_time):
            return ConversationMeetingState.EDIT_END_DATETIME

        return await save_end_datetime_and_finish(context, update, meeting, unix_time)


@HandlersRegistry.register_message(
    EditMeetingHandlerId.DURATION_END_WRONG_INPUT,
    bindable=False,
    filters=~filters.COMMAND,
)
@with_session
async def duration_end_wrong_input_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    with context.meeting_id(ContextId.EDIT_MEETING_END_DATETIME, ensure_clean=False) as meeting_id:
        user = await guards.current_user(update, session)
        meeting = await guards.meeting(session, user, meeting_id, "Duration end wrong input", context)

        error = CommonMessages.DATETIME_INVALID.get(lang=user.lang, datetime_link=build_datetime_link())
        await context.api.send_message(
            update=update, view=build_end_datetime_entry_view(meeting, user.lang, error=error)
        )
        context.put_feature_metric(
            Feature.EDIT_MEETING, name=MetricKey.ERROR, properties={"reason": "wrong_end_datetime_format"}
        )
        return ConversationMeetingState.EDIT_END_DATETIME


# --- EDIT_END_DATE state ---


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DURATION_END_DATE_NAV_CALLBACK, callback_data=cb.EDIT_MEETING_END_DATE, bindable=False
)
@with_session
async def callback_query_duration_end_date_nav(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | None:
    callback_data = guards.valid_date_callback_data(
        cb.EDIT_MEETING_END_DATE.parse(context.match), EditMeetingHandlerId.DURATION_END_DATE_NAV_CALLBACK
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, callback_data.id, "Duration end date nav", context)

    now_in_user_tz = meeting.owner.now_in_tz()
    today = now_in_user_tz.date()
    anchor_date = safe_anchor_date(meeting.end_datetime, now_in_user_tz)
    current_date = callback_data.date if today <= callback_data.date else today

    await context.api.edit_message(
        update=update,
        view=factory.edit_meeting_date_view(
            guards.render_context(user, update, context),
            meeting_id=callback_data.id,
            anchor_date=anchor_date,
            current_date=current_date,
            new=meeting.end_datetime is None,
            set_date_callback=cb.SET_MEETING_END_DATE,
            nav_callback=cb.EDIT_MEETING_END_DATE,
            back_callback=cb.EDIT_MEETING_END_DATE_TIME,
            back_button_text=ButtonMessages.END_DATE_TIME,
        ),
    )
    return ConversationMeetingState.EDIT_END_DATE


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DURATION_BACK_TO_END_DATETIME_CALLBACK,
    callback_data=cb.EDIT_MEETING_END_DATE_TIME,
    bindable=False,
)
@with_session
async def callback_query_back_to_end_datetime(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | None:
    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING_END_DATE_TIME.parse(context.match),
        EditMeetingHandlerId.DURATION_BACK_TO_END_DATETIME_CALLBACK,
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, callback_data.id, "Back to end datetime", context)

    return await show_end_datetime_entry(context, update, meeting, user.lang)


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DURATION_END_SET_DATE_CALLBACK, callback_data=cb.SET_MEETING_END_DATE, bindable=False
)
@with_session(write=True)
async def callback_query_duration_end_set_date(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    callback_data = guards.valid_date_callback_data(
        cb.SET_MEETING_END_DATE.parse(context.match), EditMeetingHandlerId.DURATION_END_SET_DATE_CALLBACK
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, callback_data.id, "Set end date in duration", context)

    if meeting.end_datetime is None:
        proposed_end = dt.datetime.combine(callback_data.date, dt.time(23, 59, tzinfo=meeting.timezone)).astimezone(
            dt.UTC
        )

        if error := validate_end_datetime(proposed_end, meeting, user.lang):
            await context.api.answer_callback_query(update, text=error, show_alert=True)
            return ConversationMeetingState.EDIT_END_DATE

        if await reject_end_beyond_horizon(context, update, meeting, proposed_end):
            return ConversationMeetingState.EDIT_END_DATE

        meeting.end_datetime = proposed_end

        return await show_end_time_prompt(context, update, meeting)

    local_end_time = to_utc(meeting.end_datetime).astimezone(meeting.timezone).time()
    proposed_end = dt.datetime.combine(callback_data.date, local_end_time, tzinfo=meeting.timezone).astimezone(dt.UTC)

    if error := validate_end_datetime(proposed_end, meeting, user.lang):
        await context.api.answer_callback_query(update, text=error, show_alert=True)
        return ConversationMeetingState.EDIT_END_DATE

    if await reject_end_beyond_horizon(context, update, meeting, proposed_end):
        return ConversationMeetingState.EDIT_END_DATE

    meeting.end_datetime = proposed_end

    return await show_end_datetime_entry(context, update, meeting, user.lang)


# --- EDIT_END_TIME state ---


async def show_end_time_prompt(
    context: TMitupContext,
    update: Update,
    meeting: Meetup,
) -> ConversationMeetingState:
    lang = meeting.lang
    assert meeting.end_datetime is not None
    datetime_entity = EntityDateTime(
        MeetingDisplayMessages.DATETIME_ENTITY_LABEL.get_text(), meeting.end_datetime, "DT"
    )
    view = MitupView(
        description=MeetingEditDurationMessages.END_DATE_ADDED_TIME_PROMPT.get(
            lang=lang, datetime=render(t"{datetime_entity}")
        ),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.DONE.get_text(lang=lang),
                    callback_data=cb.CANCEL_EDIT_MEETING_DURATION.with_id(meeting.db_id),
                )
            ]
        ],
    )
    await context.api.edit_message(update=update, view=view)
    return ConversationMeetingState.EDIT_END_TIME


def build_end_time_prompt_view(meeting: Meetup, lang: str, *, error: str | FormattedText | None = None) -> MitupView:
    """Build the end-time HH:MM prompt view (Cancel button).

    When ``error`` is given it is prepended as a leading paragraph, so a message that failed
    validation can be answered by resending this prompt with the error on top rather than a
    bare, button-less error.
    """
    description: str | FormattedText = CommonMessages.TIME_PROMPT.get(lang=lang)
    if error is not None:
        description = prepend_error(description, error)
    return MitupView(
        description=description,
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get_text(lang=lang),
                    callback_data=cb.CANCEL_EDIT_MEETING_DURATION.with_id(meeting.db_id),
                )
            ]
        ],
    )


@HandlersRegistry.register_callback_query(
    EditMeetingHandlerId.DURATION_END_TIME_CALLBACK, callback_data=cb.EDIT_MEETING_END_TIME, bindable=False
)
@with_session
async def callback_query_duration_end_time(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    callback_data = guards.valid_callback_data(
        cb.EDIT_MEETING_END_TIME.parse(context.match), EditMeetingHandlerId.DURATION_END_TIME_CALLBACK
    )
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, callback_data.id, "Edit end time in duration", context)

    await context.api.edit_message(update=update, view=build_end_time_prompt_view(meeting, user.lang))
    return ConversationMeetingState.EDIT_END_TIME


@HandlersRegistry.register_message(
    EditMeetingHandlerId.DURATION_END_SET_TIME_MESSAGE,
    bindable=False,
    filters=filters.Regex(r"^(?P<hour>\d{2}):(?P<minutes>\d{2})$"),
)
@with_session(write=True)
async def duration_end_set_time_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    time_info = cast(Match, context.match).groupdict()

    with context.meeting_id(ContextId.EDIT_MEETING_END_DATETIME, ensure_clean=False) as meeting_id:
        user = await guards.current_user(update, session)
        meeting = await guards.meeting(session, user, meeting_id, "Set end time", context)

        if not 0 <= int(time_info["hour"]) < 24 or not 0 <= int(time_info["minutes"]) < 60:
            await context.api.send_message(
                update=update,
                view=build_end_time_prompt_view(
                    meeting, user.lang, error=CommonMessages.TIME_INVALID_VALUE.get(lang=user.lang)
                ),
            )
            context.put_feature_metric(
                Feature.EDIT_MEETING, name=MetricKey.ERROR, properties={"reason": "invalid_time"}
            )
            return ConversationMeetingState.EDIT_END_TIME

        user_time = dt.time(int(time_info["hour"]), int(time_info["minutes"]), tzinfo=user.settings.tz)
        date_to_set = user.datetime_in_tz(meeting.end_datetime or dt.datetime.now(dt.UTC)).date()
        proposed_end = dt.datetime.combine(date_to_set, user_time).astimezone(dt.UTC)

        if error := validate_end_datetime(proposed_end, meeting, user.lang):
            await context.api.send_message(
                update=update, view=build_end_time_prompt_view(meeting, user.lang, error=error)
            )
            return ConversationMeetingState.EDIT_END_TIME

        if await reply_end_beyond_horizon(context, update, meeting, proposed_end):
            return ConversationMeetingState.EDIT_END_DATETIME

        return await save_end_datetime_and_finish(context, update, meeting, proposed_end)


@HandlersRegistry.register_message(
    EditMeetingHandlerId.DURATION_END_TIME_WRONG_INPUT,
    bindable=False,
    filters=~filters.COMMAND,
)
@with_session
async def duration_end_time_wrong_input_message_handler(
    session: AsyncSession, update: Update, context: TMitupContext
) -> ConversationMeetingState | int:
    with context.meeting_id(ContextId.EDIT_MEETING_END_DATETIME, ensure_clean=False) as meeting_id:
        user = await guards.current_user(update, session)
        meeting = await guards.meeting(session, user, meeting_id, "Duration end time wrong input", context)

        await context.api.send_message(
            update=update,
            view=build_end_time_prompt_view(
                meeting, user.lang, error=CommonMessages.TIME_INVALID_FORMAT.get(lang=user.lang)
            ),
        )
        context.put_feature_metric(
            Feature.EDIT_MEETING, name=MetricKey.ERROR, properties={"reason": "wrong_time_format"}
        )
        return ConversationMeetingState.EDIT_END_TIME


# --- Conversation registration ---


HandlersRegistry.register_conversation_handler(
    EditMeetingHandlerId.DURATION_CONVERSATION,
    entry_points_handler_names=[EditMeetingHandlerId.DURATION_INPUT_CALLBACK],
    states={
        ConversationMeetingState.EDIT_END_DATETIME: [
            EditMeetingHandlerId.DURATION_END_DATE_NAV_CALLBACK,
            EditMeetingHandlerId.DURATION_END_TIME_CALLBACK,
            EditMeetingHandlerId.DURATION_END_DATETIME_ENTITY_MESSAGE,
            EditMeetingHandlerId.DURATION_CANCEL_CALLBACK,
            EditMeetingHandlerId.DURATION_END_WRONG_INPUT,
            # Reachable from the beyond-horizon upsell's back button, which re-renders the End Date
            # & Time entry screen without leaving the conversation.
            EditMeetingHandlerId.DURATION_BACK_TO_END_DATETIME_CALLBACK,
        ],
        ConversationMeetingState.EDIT_END_DATE: [
            EditMeetingHandlerId.DURATION_END_DATE_NAV_CALLBACK,
            EditMeetingHandlerId.DURATION_END_SET_DATE_CALLBACK,
            EditMeetingHandlerId.DURATION_BACK_TO_END_DATETIME_CALLBACK,
        ],
        ConversationMeetingState.EDIT_END_TIME: [
            EditMeetingHandlerId.DURATION_END_SET_TIME_MESSAGE,
            EditMeetingHandlerId.DURATION_END_DATETIME_ENTITY_MESSAGE,
            EditMeetingHandlerId.DURATION_END_TIME_WRONG_INPUT,
            EditMeetingHandlerId.DURATION_CANCEL_CALLBACK,
        ],
    },
    fallbacks=[EditMeetingHandlerId.CANCEL],
)
