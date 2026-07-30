import datetime as dt
from typing import assert_never

import structlog
from telegram import Update

from mitup_bot import limits, supporter
from mitup_bot.callback_data import MeetingListSource
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import JoinedUsers, Meetup, User
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils import ButtonMessages, SupporterMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views.collaborate import supporter_upsell_view

log = structlog.get_logger(__name__)

# One event name covers every promotion off a waiting list, whatever moved the queue, so a single
# filter answers "why did this DM arrive?" for all of them. What freed the spot is the `reason`.
WAITING_LIST_PROMOTED_EVENT = "Waiting list promoted"


def log_waiting_list_promotions(meeting: Meetup, promoted_links: list[JoinedUsers], reason: str):
    """Record who a freed spot promoted, and what freed it.

    Promotion DMs the recipient without them having done anything, and nothing on the receiving end
    says which departure caused it. Promoting nobody is not an event, so an empty list writes
    nothing and the line's presence means someone moved.

    Read through the nullable `id` rather than `db_id`: the promoted rows are reached off the
    meeting mid-transaction, and a line that can raise on an unflushed row would turn a record of
    the mutation into the thing that aborts it.
    """
    if not promoted_links:
        return
    log.info(
        WAITING_LIST_PROMOTED_EVENT,
        promoted_user_ids=[link.user.id for link in promoted_links],
        promoted_count=len(promoted_links),
        reason=reason,
        n_participants=meeting.n_participants,
        effective_max_members=meeting.effective_max_members,
    )


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


async def active_meetings_cap_reached(
    user: User, update: Update, context: TMitupContext, *, back_button: ButtonConfig
) -> bool:
    """Return True, having informed the user, when they are at their active-meetings cap; else False.

    The Patron tier raises the cap; the Organizer tier is uncapped and never reaches here. The
    notice always renders as a view carrying the Collaborate button plus `back_button`: a callback
    trigger edits the tapped message in place, so the user is never left with a stale screen and an
    alert, while a message trigger (the title-message creation path) sends the view as a reply.
    """
    if not limits.at_active_meetings_cap(user):
        return False

    cap = limits.active_meetings_cap(user)
    assert cap is not None, "at_active_meetings_cap is only True when a finite cap is reached"
    # This one helper is the sole cause of three conversation ends (create entry, create title,
    # reactivate), and the user only ever sees an upsell screen, so the refusal is recorded here
    # rather than at each caller.
    log.warning(
        "Meeting action refused by active meetings cap",
        user_id=user.db_id,
        reason="active_meetings_cap",
        cap=cap,
        supporter_level=user.supporter_level.value,
        trigger="callback" if update.callback_query is not None else "message",
    )
    below_patron = not supporter.meets(user.supporter_level, SupporterLevel.HOST_2)
    message = SupporterMessages.ACTIVE_MEETINGS_CAP if below_patron else SupporterMessages.ACTIVE_MEETINGS_CAP_PATRON
    view = supporter_upsell_view(message.get(lang=user.lang, cap=cap), user.lang).with_context_menu([[back_button]])
    if update.callback_query is not None:
        await context.api.edit_message(update=update, view=view)
    else:
        await context.api.send_message(update=update, view=view)
    return True


def main_menu_back_button(lang: str) -> ButtonConfig:
    """Back-to-main-menu button for screens that replace a main-menu interaction."""
    return ButtonConfig(text=ButtonMessages.MAIN_MENU.back(lang=lang), callback_data=cb.MAIN_MENU)


def scheduling_horizon_rejection(user: User, when: dt.datetime, *, field: str) -> str | None:
    """Plain-text rejection when `when` is beyond the user's scheduling horizon, else None.

    The Patron tier raises the horizon; both tiers get the Collaborate upsell. `field` names the
    datetime being refused — `title` additionally picks the title-step wording, whose actionable
    step is resending the title rather than picking a date. Returned as plain text so it fits both
    a view description and an alert.

    Four call sites share this decision and the user sees only the upsell, so the refusal is
    recorded here.
    """
    if limits.within_scheduling_horizon(user, when):
        return None
    days = limits.scheduling_horizon_days(user)
    assert days is not None, "within_scheduling_horizon is only False when a finite horizon applies"
    log.warning(
        "Meeting datetime refused by scheduling horizon",
        user_id=user.db_id,
        reason="beyond_scheduling_horizon",
        field=field,
        requested_datetime=when,
        horizon_days=days,
        supporter_level=user.supporter_level.value,
    )
    below_patron = not supporter.meets(user.supporter_level, SupporterLevel.HOST_2)
    if field == "title":
        message = (
            SupporterMessages.SCHEDULING_HORIZON_TITLE
            if below_patron
            else SupporterMessages.SCHEDULING_HORIZON_TITLE_PATRON
        )
    else:
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
    log.warning(
        "Participant limit refused by plan cap",
        user_id=user.db_id,
        reason="participant_capacity_cap",
        requested_max=max_members,
        cap=cap,
        supporter_level=user.supporter_level.value,
    )
    return SupporterMessages.PARTICIPANT_CAPACITY.get_text(lang=user.lang, cap=cap)


def meeting_list_button(source: MeetingListSource | None, page: int, lang: str) -> ButtonConfig:
    """Button pointing at the originating list page, labelled after the list itself.

    Used by the meeting-inaccessible fallback view, where the button is an offer to browse the
    list rather than a back action. An unknown origin defaults to the active list.
    """
    if source is MeetingListSource.JOINED:
        return ButtonConfig(
            text=ButtonMessages.JOINED_MEETINGS.get_text(lang=lang),
            callback_data=cb.SHOW_JOINED_MEETINGS_PAGE.with_id(page),
        )
    return ButtonConfig(
        text=ButtonMessages.ACTIVE_MEETINGS.get_text(lang=lang),
        callback_data=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(page),
    )
