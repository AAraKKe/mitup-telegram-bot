import datetime as dt

from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Meetup
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.entities import EntityDateTime, FormattedText, build_datetime_link, render
from mitup_bot.utils.messages import (
    ButtonMessages,
    CommonMessages,
    MeetingDisplayMessages,
    MeetingEditDateTimeMessages,
    MeetingEditDurationMessages,
)
from mitup_bot.views import MitupView, RenderContext, factory
from mitup_bot.views.collaborate import supporter_upsell_view

from ..utils import prepend_error

# The screens of the When feature. Each half has the same four: an editor (Date / Time buttons), a
# calendar, a time prompt, and the upsell shown when the picked moment is past the owner's
# scheduling horizon. The upsell comes in two shapes because the two ways of reaching it differ in
# where the owner should land on the way back — the calendar they tapped, or the editor they typed
# into.


def datetime_text(value: dt.datetime) -> FormattedText:
    """Render a meeting datetime as the client-side, timezone-aware entity Telegram draws."""
    entity = EntityDateTime(MeetingDisplayMessages.DATETIME_ENTITY_LABEL.get_text(), value, "DT")
    return render(t"{entity}")


def prepend_end_cleared_notice(*, lang: str, base_message: str | FormattedText) -> FormattedText:
    return MeetingEditDateTimeMessages.END_CLEARED_BY_START.get(lang=lang).append("\n\n").append(base_message)


# --- Editors ---


def start_editor_view(
    meeting: Meetup, lang: str, today: dt.date, *, error: str | FormattedText | None = None
) -> MitupView:
    """Build the start datetime entry view (Date / Time buttons + When back button).

    When ``error`` is given it is prepended as a leading paragraph, so a message that failed
    validation can be answered by resending this prompt with the error on top rather than a
    bare, button-less error.
    """
    meeting_id = meeting.db_id
    keyboard = [
        [
            ButtonConfig(
                text=ButtonMessages.DATE.get_text(lang=lang),
                callback_data=cb.NAVIGATE_START_CALENDAR.with_id(meeting_id).with_date(today),
            ),
            ButtonConfig(
                text=ButtonMessages.TIME.get_text(lang=lang),
                callback_data=cb.OPEN_START_TIME_PROMPT.with_id(meeting_id),
            ),
        ],
    ]
    description: str | FormattedText = MeetingEditDateTimeMessages.DESCRIPTION.get(
        lang=lang, datetime_link=build_datetime_link()
    )
    if error is not None:
        description = prepend_error(description, error)
    return MitupView(description=description, keyboard=keyboard).with_back_button(
        ButtonMessages.WHEN, lang, cb.CANCEL_START_EDIT.with_id(meeting_id)
    )


def end_editor_view(meeting: Meetup, lang: str, *, error: str | FormattedText | None = None) -> MitupView:
    """Build the end datetime entry view (Date / Time buttons + When back button).

    The body names the start the span is measured from, and the end too once there is one. When
    ``error`` is given it is prepended as a leading paragraph, so a message that failed validation
    can be answered by resending this prompt with the error on top rather than a bare, button-less
    error.
    """
    assert meeting.datetime is not None
    start_text = datetime_text(meeting.datetime)
    datetime_link = build_datetime_link()
    if meeting.end_datetime is not None:
        description: str | FormattedText = MeetingEditDurationMessages.END_EDIT_PROMPT.get(
            lang=lang,
            start_datetime=start_text,
            end_datetime=datetime_text(meeting.end_datetime),
            datetime_link=datetime_link,
        )
    else:
        description = MeetingEditDurationMessages.END_PROMPT.get(
            lang=lang, start_datetime=start_text, datetime_link=datetime_link
        )
    if error is not None:
        description = prepend_error(description, error)

    meeting_id = meeting.db_id
    today = meeting.owner.now_in_tz().date()
    keyboard = [
        [
            ButtonConfig(
                text=ButtonMessages.DATE.get_text(lang=lang),
                callback_data=cb.NAVIGATE_END_CALENDAR.with_id(meeting_id).with_date(today),
            ),
            ButtonConfig(
                text=ButtonMessages.TIME.get_text(lang=lang),
                callback_data=cb.OPEN_END_TIME_PROMPT.with_id(meeting_id),
            ),
        ],
    ]
    return MitupView(description=description, keyboard=keyboard).with_back_button(
        ButtonMessages.WHEN, lang, cb.CANCEL_END_EDIT.with_id(meeting_id)
    )


# --- Calendars ---


def start_calendar_view(
    ctx: RenderContext, *, meeting_id: int, anchor_date: dt.date, current_date: dt.date, new: bool
) -> MitupView:
    return factory.edit_meeting_date_view(
        ctx,
        meeting_id=meeting_id,
        anchor_date=anchor_date,
        current_date=current_date,
        new=new,
        set_date_callback=cb.PICK_START_DATE,
        nav_callback=cb.NAVIGATE_START_CALENDAR,
        back_callback=cb.REOPEN_START_EDITOR,
        back_button_text=ButtonMessages.DATE_TIME,
    )


def end_calendar_view(
    ctx: RenderContext, *, meeting_id: int, anchor_date: dt.date, current_date: dt.date, new: bool
) -> MitupView:
    return factory.edit_meeting_date_view(
        ctx,
        meeting_id=meeting_id,
        anchor_date=anchor_date,
        current_date=current_date,
        new=new,
        set_date_callback=cb.PICK_END_DATE,
        nav_callback=cb.NAVIGATE_END_CALENDAR,
        back_callback=cb.REOPEN_END_EDITOR,
        back_button_text=ButtonMessages.END_DATE_TIME,
    )


# --- Time prompts ---


def start_time_prompt_view(meeting: Meetup, lang: str, *, error: str | FormattedText | None = None) -> MitupView:
    """Build the start-time HH:MM prompt view (Cancel button).

    A date-first flow (``meeting.datetime`` still unset) leads with the 23:59-default note, which is
    the value the date pick left behind and the owner is now replacing.
    """
    description: str | FormattedText = CommonMessages.TIME_PROMPT.get(lang=lang)
    if meeting.datetime is None:
        description = (
            MeetingEditDateTimeMessages.TIME_DATE_DEFAULT_NOTE.get(lang=lang)
            .append("\n\n")
            .append(CommonMessages.TIME_PROMPT.get(lang=lang))
        )
    if error is not None:
        description = prepend_error(description, error)
    return MitupView(
        description=description,
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get_text(lang=lang),
                    callback_data=cb.CANCEL_START_EDIT.with_id(meeting.db_id),
                )
            ]
        ],
    )


def end_time_prompt_view(meeting: Meetup, lang: str, *, error: str | FormattedText | None = None) -> MitupView:
    """Build the end-time HH:MM prompt view (Cancel button)."""
    description: str | FormattedText = CommonMessages.TIME_PROMPT.get(lang=lang)
    if error is not None:
        description = prepend_error(description, error)
    return MitupView(
        description=description,
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get_text(lang=lang),
                    callback_data=cb.CANCEL_END_EDIT.with_id(meeting.db_id),
                )
            ]
        ],
    )


# --- After a date is picked and no time has been chosen yet ---


def start_date_added_view(meeting: Meetup, lang: str, *, end_cleared: bool) -> MitupView:
    """Confirm the picked start date and ask for the time, with a Done button to keep the default."""
    assert meeting.datetime is not None
    description: str | FormattedText = MeetingEditDateTimeMessages.DATE_ADDED_TIME_PROMPT.get(
        lang=lang, datetime=datetime_text(meeting.datetime)
    )
    if end_cleared:
        description = prepend_end_cleared_notice(lang=lang, base_message=description)
    return MitupView(
        description=description,
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.DONE.get_text(lang=lang),
                    callback_data=cb.CANCEL_START_EDIT.with_id(meeting.db_id),
                )
            ]
        ],
    )


def end_date_added_view(meeting: Meetup, lang: str) -> MitupView:
    """Confirm the picked end date and ask for the time, with a Done button to keep the default."""
    assert meeting.end_datetime is not None
    return MitupView(
        description=MeetingEditDurationMessages.END_DATE_ADDED_TIME_PROMPT.get(
            lang=lang, datetime=datetime_text(meeting.end_datetime)
        ),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.DONE.get_text(lang=lang),
                    callback_data=cb.CANCEL_END_EDIT.with_id(meeting.db_id),
                )
            ]
        ],
    )


# --- Beyond-horizon upsells ---


def start_horizon_calendar_view(rejection: str, lang: str, meeting_id: int, today: dt.date) -> MitupView:
    """The upsell that replaces the calendar the owner picked from, with a way back to it."""
    return supporter_upsell_view(rejection, lang).with_context_menu(
        [
            [
                ButtonConfig(
                    text=ButtonMessages.DATE.back(lang=lang),
                    callback_data=cb.NAVIGATE_START_CALENDAR.with_id(meeting_id).with_date(today),
                )
            ]
        ]
    )


def start_horizon_reply_view(rejection: str, lang: str, meeting_id: int) -> MitupView:
    """The upsell sent as a reply to a typed start, with a way back to the editor it was typed into."""
    return supporter_upsell_view(rejection, lang).with_context_menu(
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
    """The upsell that replaces the calendar the owner picked from, with a way back to it."""
    return supporter_upsell_view(rejection, lang).with_context_menu(
        [
            [
                ButtonConfig(
                    text=ButtonMessages.DATE.back(lang=lang),
                    callback_data=cb.NAVIGATE_END_CALENDAR.with_id(meeting_id).with_date(today),
                )
            ]
        ]
    )


def end_horizon_reply_view(rejection: str, lang: str, meeting_id: int) -> MitupView:
    """The upsell sent as a reply to a typed end, with a way back to the editor it was typed into."""
    return supporter_upsell_view(rejection, lang).with_context_menu(
        [
            [
                ButtonConfig(
                    text=ButtonMessages.END_DATE_TIME.back(lang=lang),
                    callback_data=cb.REOPEN_END_EDITOR.with_id(meeting_id),
                )
            ]
        ]
    )
