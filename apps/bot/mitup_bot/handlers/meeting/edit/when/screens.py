import datetime as dt

from mitup_bot.callback_data import CallbackData, DateCallbackData
from mitup_bot.datetimes import in_timezone
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Meetup
from mitup_bot.utils import Emojis
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import (
    ButtonMessages,
    CommonMessages,
    MeetingEditDateTimeMessages,
    MeetingEditDurationMessages,
)
from mitup_bot.utils.rich_message import (
    RichContent,
    datetime_link_content,
    disabled_button_content,
    horizontal_rule_content,
)
from mitup_bot.utils.rich_template import render_rich
from mitup_bot.views import Calendar, MitupView
from mitup_bot.views.collaborate import supporter_upsell_view
from mitup_bot.views.datetime_format import datetime_content

from ..utils import prepend_error

# The screens of the When feature. Each half is one datetime card holding the calendar and the
# time row together; both share the upsell shown when the picked moment is past the owner's
# scheduling horizon, which comes in two shapes because the two ways of reaching it differ in
# where the owner should land on the way back: the screen they tapped, or the one they typed
# into.


# --- The datetime cards ---


def datetime_card(
    meeting: Meetup,
    lang: str,
    *,
    month: dt.date,
    explanation: RichContent,
    value: dt.datetime | None,
    pick_callback: DateCallbackData,
    nav_callback: DateCallbackData,
    time_callback: CallbackData,
    remove_callback: CallbackData,
    remove_label: ButtonMessages,
    cancel_callback: CallbackData,
    error: RichContent | None = None,
) -> MitupView:
    """The shared shape of both halves' cards: the explanation, the calendar with the scheduled
    day highlighted, and the time row. Day taps and the arrows edit this same message in place;
    the only typed input the screen asks for is the time, and a message carrying a full datetime
    entity is accepted whenever the screen is open.

    ``value`` is the moment the card edits (the start or the end). Without one the time row shows
    an inert "select a date first" chip, and nothing offers to remove a schedule that is not
    there: a time is a property of a scheduled day, so both controls activate once a day exists.
    """
    today = meeting.owner.now_in_tz().date()
    selected = meeting.owner.datetime_in_tz(value).date() if value else None
    calendar_part = Calendar(
        month=month,
        selected=selected,
        today=today,
        pick_callback=pick_callback,
        nav_callback=nav_callback,
        lang=lang,
    ).content

    if value is None:
        select_first = disabled_button_content(ButtonMessages.SELECT_DATE_FIRST.text(lang=lang))
        time_line = render_rich(t"{Emojis.CLOCK} {select_first}")
    else:
        edit_chip = ButtonConfig(text=ButtonMessages.EDIT.text(lang=lang), callback_data=time_callback)
        local_time = in_timezone(value, meeting.timezone).strftime("%H:%M")
        time_line = MeetingEditDateTimeMessages.CURRENT_TIME.rich(lang=lang, time=local_time, button_edit=edit_chip)

    description = explanation.append(calendar_part).append(horizontal_rule_content()).append(time_line)
    if error is not None:
        description = prepend_error(description, error)

    menu: list[list[ButtonConfig]] = []
    if value is not None:
        menu.append([ButtonConfig(text=remove_label.text(lang=lang), callback_data=remove_callback, style="danger")])
    return MitupView(message=description, menu=menu).with_back_button(ButtonMessages.MEETING, lang, cancel_callback)


def start_datetime_card(meeting: Meetup, lang: str, *, month: dt.date, error: RichContent | None = None) -> MitupView:
    """The whole start schedule on one screen; the remove button clears the full schedule through
    the clear-times confirmation, since an end cannot outlive its start."""
    meeting_id = meeting.db_id
    return datetime_card(
        meeting,
        lang,
        month=month,
        explanation=MeetingEditDateTimeMessages.EXPLANATION.rich(lang=lang, datetime_link=datetime_link_content()),
        value=meeting.datetime,
        pick_callback=cb.PICK_START_DATE.with_id(meeting_id),
        nav_callback=cb.NAVIGATE_START_CALENDAR.with_id(meeting_id),
        time_callback=cb.OPEN_START_TIME_PROMPT.with_id(meeting_id),
        remove_callback=cb.DELETE_MEETING_TIMES.with_id(meeting_id),
        remove_label=ButtonMessages.REMOVE_DATETIME,
        cancel_callback=cb.CANCEL_START_EDIT.with_id(meeting_id),
        error=error,
    )


def end_datetime_card(meeting: Meetup, lang: str, *, month: dt.date, error: RichContent | None = None) -> MitupView:
    """The end half's card: the explanation names the start the span is measured from, and the
    remove button clears the end alone through its own confirmation."""
    assert meeting.datetime is not None, "an end time is only editable once a start time exists"
    meeting_id = meeting.db_id
    start_text = datetime_content(
        meeting.datetime,
        lang=lang,
        tz=meeting.timezone,
        created=meeting.created_time,
        time_format=meeting.time_format,
    )
    return datetime_card(
        meeting,
        lang,
        month=month,
        explanation=MeetingEditDurationMessages.EXPLANATION.rich(
            lang=lang, start_datetime=start_text, datetime_link=datetime_link_content()
        ),
        value=meeting.end_datetime,
        pick_callback=cb.PICK_END_DATE.with_id(meeting_id),
        nav_callback=cb.NAVIGATE_END_CALENDAR.with_id(meeting_id),
        time_callback=cb.OPEN_END_TIME_PROMPT.with_id(meeting_id),
        remove_callback=cb.DELETE_MEETING_END_TIME.with_id(meeting_id),
        remove_label=ButtonMessages.REMOVE_END_DATETIME,
        cancel_callback=cb.CANCEL_END_EDIT.with_id(meeting_id),
        error=error,
    )


def time_prompt_view(lang: str, cancel_callback: CallbackData) -> MitupView:
    """The typed-time prompt that replaces the datetime card in place.

    Replacing the card is the point: left standing, its calendar would stay interactive above the
    fresh card the typed answer earns, as a stale near-copy of it. Cancel redraws the card, so
    backing out costs one tap.
    """
    return MitupView(
        message=CommonMessages.TIME_PROMPT.rich(lang=lang),
        menu=[[ButtonConfig(text=ButtonMessages.CANCEL.text(lang=lang), callback_data=cancel_callback)]],
    )


# --- Beyond-horizon upsells ---


def start_horizon_calendar_view(rejection: str, lang: str, meeting_id: int, today: dt.date) -> MitupView:
    """The upsell that replaces the card the owner picked from, with a way back to it."""
    return supporter_upsell_view(RichContent(rejection), lang).with_context_menu(
        [
            [
                ButtonConfig(
                    text=ButtonMessages.DATE_TIME.back(lang=lang),
                    callback_data=cb.NAVIGATE_START_CALENDAR.with_id(meeting_id).with_date(today),
                )
            ]
        ]
    )


def start_horizon_reply_view(rejection: str, lang: str, meeting_id: int) -> MitupView:
    """The upsell sent as a reply to a typed start, with a way back to the card it was typed into."""
    return supporter_upsell_view(RichContent(rejection), lang).with_context_menu(
        [
            [
                ButtonConfig(
                    text=ButtonMessages.DATE_TIME.back(lang=lang),
                    callback_data=cb.REOPEN_START_EDITOR.with_id(meeting_id),
                )
            ]
        ]
    )


def end_horizon_calendar_view(rejection: str, lang: str, meeting_id: int, today: dt.date) -> MitupView:
    """The upsell that replaces the card the owner picked from, with a way back to it."""
    return supporter_upsell_view(RichContent(rejection), lang).with_context_menu(
        [
            [
                ButtonConfig(
                    text=ButtonMessages.END_DATE_TIME.back(lang=lang),
                    callback_data=cb.NAVIGATE_END_CALENDAR.with_id(meeting_id).with_date(today),
                )
            ]
        ]
    )


def end_horizon_reply_view(rejection: str, lang: str, meeting_id: int) -> MitupView:
    """The upsell sent as a reply to a typed end, with a way back to the card it was typed into."""
    return supporter_upsell_view(RichContent(rejection), lang).with_context_menu(
        [
            [
                ButtonConfig(
                    text=ButtonMessages.END_DATE_TIME.back(lang=lang),
                    callback_data=cb.REOPEN_END_EDITOR.with_id(meeting_id),
                )
            ]
        ]
    )
