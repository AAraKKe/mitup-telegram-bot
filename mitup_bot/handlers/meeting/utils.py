import datetime as dt
from typing import assert_never

from telegram import Update

from mitup_bot import guards, limits, supporter
from mitup_bot.callback_data import MeetingListSource
from mitup_bot.models import User
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils import ButtonMessages, SupporterMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import ButtonConfig


def meeting_detail_back_button(source: MeetingListSource | None, page: int, lang: str) -> ButtonConfig:
    """Back button for a meeting detail screen, targeting the list page the user came from.

    Falls back to the main menu when the originating list is unknown (e.g. reaching the detail
    from an edit flow rather than a list).
    """
    match source:
        case MeetingListSource.ACTIVE:
            return ButtonConfig(
                text=ButtonMessages.ACTIVE_MEETINGS.back(lang=lang),
                callback_data=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(page),
            )
        case MeetingListSource.JOINED:
            return ButtonConfig(
                text=ButtonMessages.JOINED_MEETINGS.back(lang=lang),
                callback_data=cb.SHOW_JOINED_MEETINGS_PAGE.with_id(page),
            )
        case None:
            return ButtonConfig(
                text=ButtonMessages.MAIN_MENU.back(lang=lang),
                callback_data=cb.MAIN_MENU,
            )
        case _ as unreachable:
            assert_never(unreachable)


async def active_meetings_cap_reached(user: User, update: Update, context: TMitupContext) -> bool:
    """Return True, having informed the user, when they are at their active-meetings cap; else False.

    The Patron tier raises the cap: a below-Patron user at the free cap is shown the upsell via
    `supporter_required` (pointing to Collaborate), while a Patron at the sanity cap gets a plain
    limit notice (the Organizer tier is uncapped and never reaches here). The notice rides a
    callback-query alert when one is present (the New Meeting button and the reactivation button) and
    a sent message otherwise (the title-message creation path).
    """
    if not limits.at_active_meetings_cap(user):
        return False

    cap = limits.active_meetings_cap(user)
    assert cap is not None, "at_active_meetings_cap is only True when a finite cap is reached"
    below_patron = not supporter.meets(user.supporter_level, SupporterLevel.HOST_2)
    if update.callback_query is not None:
        if below_patron:
            await guards.supporter_required(
                user, update, context, SupporterMessages.ACTIVE_MEETINGS_CAP, minimum=SupporterLevel.HOST_2, cap=cap
            )
        else:
            await context.api.answer_callback_query(
                update,
                text=SupporterMessages.ACTIVE_MEETINGS_CAP_PATRON.get_text(lang=user.lang, cap=cap),
                show_alert=True,
            )
    else:
        message = (
            SupporterMessages.ACTIVE_MEETINGS_CAP if below_patron else SupporterMessages.ACTIVE_MEETINGS_CAP_PATRON
        )
        await context.api.send_message(update=update, view=message.get(lang=user.lang, cap=cap))
    return True


def scheduling_horizon_rejection(user: User, when: dt.datetime) -> str | None:
    """Plain-text rejection when `when` is beyond the user's scheduling horizon, else None.

    The Patron tier raises the horizon; a below-Patron user is pointed at Collaborate. Returned as
    plain text so it fits both a callback-query alert and a sent message.
    """
    if limits.within_scheduling_horizon(user, when):
        return None
    days = limits.scheduling_horizon_days(user)
    assert days is not None, "within_scheduling_horizon is only False when a finite horizon applies"
    below_patron = not supporter.meets(user.supporter_level, SupporterLevel.HOST_2)
    message = SupporterMessages.SCHEDULING_HORIZON if below_patron else SupporterMessages.SCHEDULING_HORIZON_PATRON
    return message.get_text(lang=user.lang, days=days)


def participant_capacity_rejection(user: User, max_members: int) -> str | None:
    """Plain-text rejection when a capped owner sets a participant limit above their cap, else None.

    Patron and Organizer owners are uncapped, so they never hit this; a capped owner is pointed at
    Collaborate. Returned as plain text so it fits both a sent message and a callback-query alert.
    """
    cap = limits.participant_capacity(user)
    if cap is None or max_members <= cap:
        return None
    return SupporterMessages.PARTICIPANT_CAPACITY.get_text(lang=user.lang, cap=cap)


def meeting_list_button(source: MeetingListSource | None, page: int, lang: str) -> ButtonConfig:
    """Button pointing at the originating list page, labelled after the list itself.

    Used by the meeting-inaccessible fallback view, where the button is an offer to browse the
    list rather than a back action. An unknown origin defaults to the active list.
    """
    if source is MeetingListSource.JOINED:
        return ButtonConfig(
            text=ButtonMessages.JOINED_MEETINGS.get(lang=lang),
            callback_data=cb.SHOW_JOINED_MEETINGS_PAGE.with_id(page),
        )
    return ButtonConfig(
        text=ButtonMessages.ACTIVE_MEETINGS.get(lang=lang),
        callback_data=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(page),
    )
