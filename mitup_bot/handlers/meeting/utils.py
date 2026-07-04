from typing import assert_never

from mitup_bot.callback_data import MeetingListSource
from mitup_bot.utils import ButtonMessages
from mitup_bot.utils import callbacks as cb
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
