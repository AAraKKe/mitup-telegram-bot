import datetime as dt
from typing import assert_never

from telegram import Update

from mitup_bot import guards, limits
from mitup_bot.callback_data import MeetingListSource
from mitup_bot.models import User
from mitup_bot.utils import ButtonMessages, PremiumMessages
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

    Premium raises the cap: a free user at the cap is shown the upsell via `premium_required`
    (pointing to Collaborate), while a premium user at the sanity cap gets a plain limit notice.
    The notice rides a callback-query alert when one is present (the New Meeting button and the
    reactivation button) and a sent message otherwise (the title-message creation path).
    """
    if not limits.at_active_meetings_cap(user):
        return False

    cap = limits.active_meetings_cap(user)
    if update.callback_query is not None:
        if not user.is_premium:
            await guards.premium_required(user, update, context, PremiumMessages.ACTIVE_MEETINGS_CAP, cap=cap)
        else:
            await context.api.answer_callback_query(
                update,
                text=PremiumMessages.ACTIVE_MEETINGS_CAP_PREMIUM.get_text(lang=user.lang, cap=cap),
                show_alert=True,
            )
    else:
        message = (
            PremiumMessages.ACTIVE_MEETINGS_CAP if not user.is_premium else PremiumMessages.ACTIVE_MEETINGS_CAP_PREMIUM
        )
        await context.api.send_message(update=update, view=message.get(lang=user.lang, cap=cap))
    return True


def scheduling_horizon_rejection(user: User, when: dt.datetime) -> str | None:
    """Plain-text rejection when `when` is beyond the user's scheduling horizon, else None.

    Premium raises the horizon; a free user is pointed at Collaborate. Returned as plain text so it
    fits both a callback-query alert and a sent message.
    """
    if limits.within_scheduling_horizon(user, when):
        return None
    days = limits.scheduling_horizon_days(user)
    message = PremiumMessages.SCHEDULING_HORIZON if not user.is_premium else PremiumMessages.SCHEDULING_HORIZON_PREMIUM
    return message.get_text(lang=user.lang, days=days)


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
