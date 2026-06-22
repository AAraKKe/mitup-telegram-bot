"""
This module contains tests for common failure modes for all handlers. The intention is that we remove the need of
repeating the same kind of tests for every hanlder that should behave the same.

We just need to update the factory methods that produces the parameters for each test case.
"""

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

import pytest
from telegram import Location, MessageEntity, Update

from mitup_bot.custom_context import ContextId
from mitup_bot.handler_id import HandlerId
from mitup_bot.handlers.edit_settings.enums import EditSettingsHandlerId
from mitup_bot.handlers.main_menu.enums import MainMenuHandlerId
from mitup_bot.handlers.meeting.edit.enums import EditMeetingHandlerId
from mitup_bot.handlers.meeting.enums import MeetingHandlerId
from mitup_bot.handlers.stale_cancel import StaleCancelHandlerId
from mitup_bot.models import User
from mitup_bot.monitoring import MetricKey, MetricUnit
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, CommonMessages
from mitup_bot.views import ButtonConfig, Keyboard, MitupView, factory
from tests.helpers import AnyFloat, HandlerContext, UpdateRequest, call_handler, create_meetup
from tests.helpers.monitoring import MetricAssertions
from tests.helpers.stub_db import MockDbSession

MEETING_ID_NOT_OWNED = 99
MEETING_ID_NOT_FOUND = 9999
MEETING_ID_INACTIVE = 88


class _Unset:
    """Sentinel type for dataclass fields that distinguish 'not set' from None."""


_UNSET: Final = _Unset()


class ErrorMode(Enum):
    """
    Enum to define the different error modes that can be tested
    """

    MEETING_NOT_OWNED = "MeetingNotOwned"
    USER_NOT_FOUND = "UserNotFound"
    MEETING_NOT_FOUND = "MeetingNotFound"
    MALFORMED_CALLBACK_DATA = "MalformedCallbackData"
    MISSING_USER_DATA = "MissingUserData"
    MEETING_INACTIVE_OWNER = "MeetingInactiveOwner"


@dataclass
class Context:
    handler_id: HandlerId
    update_request: UpdateRequest
    id: str
    error_modes: set[ErrorMode]
    user_fixture: str = "user_with_settings"
    exception: Exception | None = None
    fault_count: int = 0  # This is the value of the fault metric (both with and without prefix)
    custom_keyboard: Keyboard | None = None  # Used when the meeting does not exist and the message is edited
    reactivation_back_keyboard_factory: Callable[[str], Keyboard] | None = (
        None  # Lang-dependent back row for the reactivation prompt
    )
    shows_deleted_message_when_not_found: bool = True  # False for handlers using user_owns_meeting directly
    meeting_id: dict[ContextId, int] | None = None  # Meeting id to store in the context data
    # Extra metric emissions for this handler (e.g. CleanUserData). Each is a (name, times) pair.
    extra_metrics: list[tuple[str, int]] = field(default_factory=list)
    # Override extra_metrics when the meeting is not found. Uses _UNSET sentinel as default (falls back to
    # extra_metrics). Set to [] to explicitly assert no extra metrics.
    extra_metrics_not_found: list[tuple[str, int]] | None | _Unset = field(default_factory=_Unset)
    # Override extra_metrics for the non-owner inactive meeting test.
    extra_metrics_non_owner_inactive: list[tuple[str, int]] | None | _Unset = field(default_factory=_Unset)


CONTEXTS = [
    Context(
        handler_id=EditMeetingHandlerId.DATE_TIME_ENTRY_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SET_MEETING_START_TIME.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="edit_meeting_date_time_entry",
    ),
    Context(
        handler_id=EditMeetingHandlerId.DATE_CALLBACK,
        update_request=UpdateRequest(
            callback_query=cb.EDIT_MEETING_DATE.with_id(MEETING_ID_NOT_OWNED).with_date(dt.date(2024, 12, 21))
        ),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="edit_meeting_date",
    ),
    Context(
        handler_id=EditMeetingHandlerId.SET_DATE_CALLBACK,
        update_request=UpdateRequest(
            callback_query=cb.SET_MEETING_DATE.with_id(MEETING_ID_NOT_OWNED).with_date(dt.date(2024, 12, 21))
        ),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="set_meeting_date",
    ),
    Context(
        handler_id=EditMeetingHandlerId.EDIT_TIME_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_TIME.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="edit_meeting_time",
    ),
    # CANCEL_START_TIME: calls cleanup_states when meeting is not accessible (not when user not found).
    Context(
        handler_id=EditMeetingHandlerId.CANCEL_START_TIME_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.CANCEL_EDIT_START_TIME.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED},
        id="cancel_start_time",
        extra_metrics=[("CleanUserData", 7)],
        extra_metrics_not_found=[("CleanUserData", 7)],
    ),
    Context(
        handler_id=EditMeetingHandlerId.CANCEL_START_TIME_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.CANCEL_EDIT_START_TIME.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="cancel_start_time",
    ),
    Context(
        handler_id=EditMeetingHandlerId.BACK_TO_EDIT_DATETIME_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="back_to_edit_datetime_from_calendar",
    ),
    Context(
        handler_id=EditMeetingHandlerId.SET_TIME_MESSAGE,
        update_request=UpdateRequest(message_text="12:00"),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="set_meeting_time_message",
        extra_metrics=[("CleanUserData", 1)],
        meeting_id={ContextId.EDIT_MEETING_TIME: 99},
    ),
    Context(
        handler_id=EditMeetingHandlerId.WRONG_TIME_FORMAT,
        update_request=UpdateRequest(message_text="12:00"),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="wrong_time_format",
    ),
    Context(
        handler_id=EditMeetingHandlerId.DATETIME_WRONG_TEXT_FORMAT,
        update_request=UpdateRequest(message_text="some text"),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="datetime_wrong_text_format",
    ),
    Context(
        handler_id=EditMeetingHandlerId.DATETIME_WRONG_MESSAGE,
        update_request=UpdateRequest(location=Location(latitude=0, longitude=0)),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="datetime_wrong_message",
    ),
    Context(
        handler_id=MeetingHandlerId.CREATE_MEETING_TITLE_MESSAGE,
        update_request=UpdateRequest(message_text="My Meeting"),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="create_meeting_title_message",
    ),
    Context(
        handler_id=MeetingHandlerId.CREATE_MEETING_INVALID_TITLE_MESSAGE,
        update_request=UpdateRequest(message_text="My Meeting"),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="create_meeting_invalid_title_message",
    ),
    Context(
        handler_id=EditSettingsHandlerId.LANGUAGE_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_LANGUAGE),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="user_not_found_edit_language",
    ),
    Context(
        handler_id=EditSettingsHandlerId.SET_DEFAULT_LOCK_ON_START,
        update_request=UpdateRequest(callback_query=cb.SET_DEFAULT_LOCK_ON_START),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="user_not_found_set_default_lock_on_start",
    ),
    Context(
        handler_id=EditMeetingHandlerId.SET_TIME_MESSAGE,
        update_request=UpdateRequest(message_text="12:00"),
        error_modes={ErrorMode.MISSING_USER_DATA},
        id="set_meeting_time_message",
    ),
    Context(
        handler_id=EditMeetingHandlerId.DATE_TIME_ENTRY_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SET_MEETING_START_TIME),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="edit_meeting_date_time_entry",
    ),
    Context(
        handler_id=EditMeetingHandlerId.DATE_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_DATE.with_date(dt.date(2024, 12, 21))),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="edit_meeting_date",
    ),
    Context(
        handler_id=EditMeetingHandlerId.SET_DATE_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SET_MEETING_DATE.with_date(dt.date(2024, 12, 21))),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="set_meeting_date",
    ),
    Context(
        handler_id=EditMeetingHandlerId.EDIT_TIME_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_TIME),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="edit_meeting_time",
    ),
    Context(
        handler_id=EditMeetingHandlerId.CANCEL_START_TIME_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.CANCEL_EDIT_START_TIME),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="cancel_start_time_malformed",
    ),
    Context(
        handler_id=EditMeetingHandlerId.BACK_TO_EDIT_DATETIME_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="back_to_edit_datetime_from_calendar_malformed",
    ),
    Context(
        handler_id=EditMeetingHandlerId.MEETING_SETTINGS_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_SETTINGS.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED},
        id="edit_meeting_settings",
    ),
    Context(
        handler_id=EditMeetingHandlerId.SET_MEETING_WAITING_LIST_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SET_MEETING_WAITING_LIST.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED},
        id="set_meeting_waiting_list",
    ),
    Context(
        handler_id=EditMeetingHandlerId.SET_MEETING_PUBLIC_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SET_MEETING_PUBLIC.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED},
        id="set_meeting_public",
    ),
    Context(
        handler_id=EditMeetingHandlerId.SET_MEETING_INCOGNITO_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SET_MEETING_INCOGNITO.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED},
        id="set_meeting_incognito",
    ),
    Context(
        handler_id=EditMeetingHandlerId.SET_MEETING_ALLOW_INVITATIONS_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SET_MEETING_ALLOW_INVITATIONS.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED},
        id="set_meeting_allow_invitations",
    ),
    Context(
        handler_id=EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_CALLBACK,
        update_request=UpdateRequest(
            callback_query=cb.EDIT_MEETING_KICK_OUT_PARTICIPANTS.with_ids(MEETING_ID_NOT_FOUND, 1)
        ),
        error_modes={ErrorMode.MEETING_NOT_FOUND},
        id="edit_meeting_participants_kick_out",
    ),
    Context(
        handler_id=EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_CALLBACK,
        update_request=UpdateRequest(
            callback_query=cb.EDIT_MEETING_KICK_OUT_PARTICIPANTS.with_ids(MEETING_ID_NOT_OWNED, 1)
        ),
        error_modes={ErrorMode.MEETING_NOT_OWNED},
        id="edit_meeting_participants_kick_out",
    ),
    Context(
        handler_id=EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_ACTION_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_KICK_OUT_ACTION.with_ids(MEETING_ID_NOT_OWNED, 1)),
        error_modes={ErrorMode.MEETING_NOT_OWNED},
        id="edit_meeting_participants_kick_out",
    ),
    Context(
        handler_id=EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_ACTION_CONFIRM_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.CONFIRM_KICK_OUT.with_ids(MEETING_ID_NOT_OWNED, 1)),
        error_modes={ErrorMode.MEETING_NOT_OWNED},
        id="edit_meeting_participants_kick_out_confirm",
    ),
    Context(
        handler_id=EditMeetingHandlerId.LANGUAGE_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_LANGUAGE.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="edit_meeting_language",
    ),
    Context(
        handler_id=EditMeetingHandlerId.SET_LANGUAGE_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SET_MEETING_LANGUAGE.with_ids(MEETING_ID_NOT_OWNED, 0)),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="set_meeting_language",
    ),
    Context(
        handler_id=EditMeetingHandlerId.LANGUAGE_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_LANGUAGE),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="edit_meeting_language_malformed",
    ),
    Context(
        handler_id=EditMeetingHandlerId.SET_LANGUAGE_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SET_MEETING_LANGUAGE),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="set_meeting_language_malformed",
    ),
    Context(
        handler_id=MeetingHandlerId.ATTACH_TO_CHAT,
        update_request=UpdateRequest(callback_query=cb.ATTACH_TO_CHAT),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="attach_to_chat_malformed",
    ),
    Context(
        handler_id=MainMenuHandlerId.SHOW_PAST_MEETINGS_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.PAST_MEETINGS),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="show_past_meetings",
    ),
    Context(
        handler_id=MainMenuHandlerId.SHOW_PAST_MEETING_PAGE_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SHOW_PAST_MEETING_PAGE.with_id(1)),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="show_past_meeting_page",
    ),
    Context(
        handler_id=MainMenuHandlerId.SHOW_PAST_MEETING_PAGE_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SHOW_PAST_MEETING_PAGE),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="show_past_meeting_page_malformed",
    ),
    Context(
        handler_id=MeetingHandlerId.SHOW_PAST_MEETING_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SHOW_PAST_MEETING.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="show_past_meeting",
        shows_deleted_message_when_not_found=False,
    ),
    Context(
        handler_id=MeetingHandlerId.SHOW_PAST_MEETING_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SHOW_PAST_MEETING),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="show_past_meeting_malformed",
    ),
    Context(
        handler_id=MeetingHandlerId.REACTIVATE_MEETING_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.REACTIVATE_MEETING.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="reactivate_meeting",
        shows_deleted_message_when_not_found=False,
    ),
    Context(
        handler_id=MeetingHandlerId.REACTIVATE_MEETING_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.REACTIVATE_MEETING),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="reactivate_meeting_malformed",
    ),
    Context(
        handler_id=MeetingHandlerId.DELETE_PAST_MEETING_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.DELETE_PAST_MEETING.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="delete_past_meeting",
        shows_deleted_message_when_not_found=False,
    ),
    Context(
        handler_id=MeetingHandlerId.DELETE_PAST_MEETING_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.DELETE_PAST_MEETING),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="delete_past_meeting_malformed",
    ),
    Context(
        handler_id=MeetingHandlerId.CONFIRM_DELETE_PAST_MEETING_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.CONFIRM_DELETE_PAST_MEETING.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="confirm_delete_past_meeting",
        shows_deleted_message_when_not_found=False,
    ),
    Context(
        handler_id=MeetingHandlerId.CONFIRM_DELETE_PAST_MEETING_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.CONFIRM_DELETE_PAST_MEETING),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="confirm_delete_past_meeting_malformed",
    ),
    Context(
        handler_id=MeetingHandlerId.DECLINE_DELETE_PAST_MEETING_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.DECLINE_DELETE_PAST_MEETING.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="decline_delete_past_meeting",
        shows_deleted_message_when_not_found=False,
    ),
    Context(
        handler_id=MeetingHandlerId.DECLINE_DELETE_PAST_MEETING_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.DECLINE_DELETE_PAST_MEETING),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="decline_delete_past_meeting_malformed",
    ),
    # --- Inactive meeting accessed by owner (meeting_accessible handlers) ---
    Context(
        handler_id=MeetingHandlerId.SHOW_MEETING_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SHOW_MEETING.with_id(MEETING_ID_INACTIVE)),
        error_modes={ErrorMode.MEETING_INACTIVE_OWNER},
        id="show_inactive_meeting",
        reactivation_back_keyboard_factory=lambda lang: [
            [
                ButtonConfig(
                    text=ButtonMessages.ACTIVE_MEETINGS.get(lang=lang),
                    callback_data=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(1),
                )
            ]
        ],
    ),
    Context(
        handler_id=EditMeetingHandlerId.EDIT,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING.with_id(MEETING_ID_INACTIVE)),
        error_modes={ErrorMode.MEETING_INACTIVE_OWNER},
        id="edit_inactive_meeting",
    ),
    Context(
        handler_id=EditMeetingHandlerId.TITLE_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_TITLE.with_id(MEETING_ID_INACTIVE)),
        error_modes={ErrorMode.MEETING_INACTIVE_OWNER},
        id="edit_inactive_meeting_title",
    ),
    Context(
        handler_id=EditMeetingHandlerId.MEETING_SETTINGS_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_SETTINGS.with_id(MEETING_ID_INACTIVE)),
        error_modes={ErrorMode.MEETING_INACTIVE_OWNER},
        id="edit_inactive_meeting_settings",
    ),
    Context(
        handler_id=EditMeetingHandlerId.DATE_TIME_ENTRY_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SET_MEETING_START_TIME.with_id(MEETING_ID_INACTIVE)),
        error_modes={ErrorMode.MEETING_INACTIVE_OWNER},
        id="edit_inactive_meeting_date_time_entry",
    ),
    Context(
        handler_id=EditMeetingHandlerId.DATE_CALLBACK,
        update_request=UpdateRequest(
            callback_query=cb.EDIT_MEETING_DATE.with_id(MEETING_ID_INACTIVE).with_date(dt.date(2024, 12, 21))
        ),
        error_modes={ErrorMode.MEETING_INACTIVE_OWNER},
        id="edit_inactive_meeting_date",
    ),
    Context(
        handler_id=EditMeetingHandlerId.SET_DATE_CALLBACK,
        update_request=UpdateRequest(
            callback_query=cb.SET_MEETING_DATE.with_id(MEETING_ID_INACTIVE).with_date(dt.date(2024, 12, 21))
        ),
        error_modes={ErrorMode.MEETING_INACTIVE_OWNER},
        id="set_inactive_meeting_date",
    ),
    Context(
        handler_id=EditMeetingHandlerId.EDIT_TIME_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_TIME.with_id(MEETING_ID_INACTIVE)),
        error_modes={ErrorMode.MEETING_INACTIVE_OWNER},
        id="edit_inactive_meeting_time",
    ),
    Context(
        handler_id=EditMeetingHandlerId.CANCEL_START_TIME_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.CANCEL_EDIT_START_TIME.with_id(MEETING_ID_INACTIVE)),
        error_modes={ErrorMode.MEETING_INACTIVE_OWNER},
        id="cancel_start_time_inactive",
        extra_metrics=[("CleanUserData", 7)],
        extra_metrics_non_owner_inactive=[("CleanUserData", 7)],
    ),
    Context(
        handler_id=EditMeetingHandlerId.BACK_TO_EDIT_DATETIME_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING.with_id(MEETING_ID_INACTIVE)),
        error_modes={ErrorMode.MEETING_INACTIVE_OWNER},
        id="back_to_edit_datetime_from_calendar_inactive",
    ),
    Context(
        handler_id=EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_CALLBACK,
        update_request=UpdateRequest(
            callback_query=cb.EDIT_MEETING_KICK_OUT_PARTICIPANTS.with_ids(MEETING_ID_INACTIVE, 1)
        ),
        error_modes={ErrorMode.MEETING_INACTIVE_OWNER},
        id="kickout_inactive_meeting",
    ),
    Context(
        handler_id=EditMeetingHandlerId.SET_MEETING_WAITING_LIST_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SET_MEETING_WAITING_LIST.with_id(MEETING_ID_INACTIVE)),
        error_modes={ErrorMode.MEETING_INACTIVE_OWNER},
        id="set_inactive_meeting_waiting_list",
    ),
    Context(
        handler_id=EditMeetingHandlerId.SET_MEETING_PUBLIC_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SET_MEETING_PUBLIC.with_id(MEETING_ID_INACTIVE)),
        error_modes={ErrorMode.MEETING_INACTIVE_OWNER},
        id="set_inactive_meeting_public",
    ),
    Context(
        handler_id=EditMeetingHandlerId.DURATION_INPUT_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SET_MEETING_END_TIME.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="set_meeting_end_time",
    ),
    Context(
        handler_id=EditMeetingHandlerId.LOCK_ON_START_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SET_MEETING_LOCK_ON_START.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="set_meeting_lock_on_start",
    ),
    Context(
        handler_id=EditMeetingHandlerId.DURATION_CANCEL_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.CANCEL_EDIT_MEETING_DURATION.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="cancel_edit_meeting_duration",
    ),
    Context(
        handler_id=EditMeetingHandlerId.DURATION_CANCEL_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.CANCEL_EDIT_MEETING_DURATION),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="cancel_edit_meeting_duration_malformed",
    ),
    # New end-datetime conversation handlers
    Context(
        handler_id=EditMeetingHandlerId.DURATION_END_DATE_NAV_CALLBACK,
        update_request=UpdateRequest(
            callback_query=cb.EDIT_MEETING_END_DATE.with_id(MEETING_ID_NOT_OWNED).with_date(dt.date(2024, 12, 21))
        ),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="duration_end_date_nav",
    ),
    Context(
        handler_id=EditMeetingHandlerId.DURATION_END_SET_DATE_CALLBACK,
        update_request=UpdateRequest(
            callback_query=cb.SET_MEETING_END_DATE.with_id(MEETING_ID_NOT_OWNED).with_date(dt.date(2024, 12, 21))
        ),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="duration_end_set_date",
    ),
    Context(
        handler_id=EditMeetingHandlerId.DURATION_END_TIME_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_END_TIME.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="duration_end_time",
    ),
    Context(
        handler_id=EditMeetingHandlerId.DURATION_END_DATE_NAV_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_END_DATE.with_date(dt.date(2024, 12, 21))),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="duration_end_date_nav_malformed",
    ),
    Context(
        handler_id=EditMeetingHandlerId.DURATION_END_SET_DATE_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SET_MEETING_END_DATE.with_date(dt.date(2024, 12, 21))),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="duration_end_set_date_malformed",
    ),
    Context(
        handler_id=EditMeetingHandlerId.DURATION_END_TIME_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_END_TIME),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="duration_end_time_malformed",
    ),
    # Duration end sub-flow message handlers
    Context(
        handler_id=EditMeetingHandlerId.DURATION_END_SET_TIME_MESSAGE,
        update_request=UpdateRequest(message_text="11:30"),
        error_modes={ErrorMode.USER_NOT_FOUND, ErrorMode.MEETING_NOT_OWNED},
        id="duration_end_set_time_message",
        meeting_id={ContextId.EDIT_MEETING_END_DATETIME: 99},
    ),
    Context(
        handler_id=EditMeetingHandlerId.DURATION_END_SET_TIME_MESSAGE,
        update_request=UpdateRequest(message_text="11:30"),
        error_modes={ErrorMode.MISSING_USER_DATA},
        id="duration_end_set_time_message",
    ),
    Context(
        handler_id=EditMeetingHandlerId.DURATION_END_WRONG_INPUT,
        update_request=UpdateRequest(message_text="some text"),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="duration_end_wrong_input",
    ),
    Context(
        handler_id=EditMeetingHandlerId.DURATION_END_TIME_WRONG_INPUT,
        update_request=UpdateRequest(message_text="bad time"),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="duration_end_time_wrong_input",
    ),
    # Duration entity message handlers — require a message with a date_time entity
    Context(
        handler_id=EditMeetingHandlerId.DURATION_END_DATETIME_ENTITY_MESSAGE,
        update_request=UpdateRequest(
            message_text="Tomorrow at noon",
            entities=[
                MessageEntity(
                    type=MessageEntity.DATE_TIME,
                    offset=0,
                    length=16,
                    unix_time=dt.datetime(2023, 11, 14, 22, 13, 20, tzinfo=dt.UTC),
                )
            ],
        ),
        error_modes={ErrorMode.USER_NOT_FOUND, ErrorMode.MEETING_NOT_OWNED},
        id="duration_end_datetime_entity_message",
        meeting_id={ContextId.EDIT_MEETING_END_DATETIME: 99},
    ),
    Context(
        handler_id=EditMeetingHandlerId.DURATION_END_DATETIME_ENTITY_MESSAGE,
        update_request=UpdateRequest(
            message_text="Tomorrow at noon",
            entities=[
                MessageEntity(
                    type=MessageEntity.DATE_TIME,
                    offset=0,
                    length=16,
                    unix_time=dt.datetime(2023, 11, 14, 22, 13, 20, tzinfo=dt.UTC),
                )
            ],
        ),
        error_modes={ErrorMode.MISSING_USER_DATA},
        id="duration_end_datetime_entity_message",
    ),
    # --- When screen handlers ---
    Context(
        handler_id=EditMeetingHandlerId.WHEN_ENTRY_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_WHEN.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="when_entry",
    ),
    Context(
        handler_id=EditMeetingHandlerId.WHEN_ENTRY_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_WHEN),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="when_entry_malformed",
    ),
    Context(
        handler_id=EditMeetingHandlerId.WHEN_ENTRY_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_WHEN.with_id(MEETING_ID_INACTIVE)),
        error_modes={ErrorMode.MEETING_INACTIVE_OWNER},
        id="when_entry_inactive",
    ),
    Context(
        handler_id=EditMeetingHandlerId.CLEAR_TIMES_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.DELETE_MEETING_TIMES.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="clear_times",
    ),
    Context(
        handler_id=EditMeetingHandlerId.CLEAR_TIMES_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.DELETE_MEETING_TIMES),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="clear_times_malformed",
    ),
    Context(
        handler_id=EditMeetingHandlerId.CLEAR_TIMES_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.DELETE_MEETING_TIMES.with_id(MEETING_ID_INACTIVE)),
        error_modes={ErrorMode.MEETING_INACTIVE_OWNER},
        id="clear_times_inactive",
    ),
    Context(
        handler_id=EditMeetingHandlerId.CONFIRM_CLEAR_TIMES_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.CONFIRM_DELETE_MEETING_TIMES.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="confirm_clear_times",
    ),
    Context(
        handler_id=EditMeetingHandlerId.CONFIRM_CLEAR_TIMES_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.CONFIRM_DELETE_MEETING_TIMES),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="confirm_clear_times_malformed",
    ),
    Context(
        handler_id=EditMeetingHandlerId.CONFIRM_CLEAR_TIMES_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.CONFIRM_DELETE_MEETING_TIMES.with_id(MEETING_ID_INACTIVE)),
        error_modes={ErrorMode.MEETING_INACTIVE_OWNER},
        id="confirm_clear_times_inactive",
    ),
    Context(
        handler_id=EditMeetingHandlerId.DECLINE_CLEAR_TIMES_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.DECLINE_DELETE_MEETING_TIMES.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="decline_clear_times",
    ),
    Context(
        handler_id=EditMeetingHandlerId.DECLINE_CLEAR_TIMES_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.DECLINE_DELETE_MEETING_TIMES),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="decline_clear_times_malformed",
    ),
    Context(
        handler_id=EditMeetingHandlerId.DECLINE_CLEAR_TIMES_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.DECLINE_DELETE_MEETING_TIMES.with_id(MEETING_ID_INACTIVE)),
        error_modes={ErrorMode.MEETING_INACTIVE_OWNER},
        id="decline_clear_times_inactive",
    ),
    Context(
        handler_id=StaleCancelHandlerId.STALE_CANCEL_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.CANCEL_CREATE_MEETING),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="stale_cancel",
    ),
]


# -------------------
# Factory methods
# -------------------
def handler_stop_for_accessing_meeting_not_owned_factory() -> list[Context]:
    return [context for context in CONTEXTS if ErrorMode.MEETING_NOT_OWNED in context.error_modes]


def handler_stops_when_user_not_found() -> list[Context]:
    return [context for context in CONTEXTS if ErrorMode.USER_NOT_FOUND in context.error_modes]


def handler_stops_when_meeting_not_found() -> list[Context]:
    return [context for context in CONTEXTS if ErrorMode.MEETING_NOT_FOUND in context.error_modes]


def handler_stops_due_to_missing_user_data() -> list[Context]:
    return [context for context in CONTEXTS if ErrorMode.MISSING_USER_DATA in context.error_modes]


def handler_stops_due_to_malformed_callback_data() -> list[Context]:
    return [context for context in CONTEXTS if ErrorMode.MALFORMED_CALLBACK_DATA in context.error_modes]


def handler_shows_reactivation_prompt_for_inactive_meeting() -> list[Context]:
    return [context for context in CONTEXTS if ErrorMode.MEETING_INACTIVE_OWNER in context.error_modes]


def _assert_handler_metrics(
    metrics: MetricAssertions,
    *,
    fault_value: int,
    extra_metrics: list[tuple[str, int]] | None = None,
) -> None:
    """Assert the standard handler metrics emitted by callback_with_metrics."""
    # FAULT: emit_global=True means 2 records (with handler dims + without)
    metrics.assert_emitted(name=MetricKey.FAULT, value=fault_value, times=2)
    # TIME: emit_global=True means 2 records
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=2)
    # DB_CONNECTIONS_LEAKED: emit_global=True means 2 records
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=2)
    # Extra metrics this handler emits
    for metric_name, times in extra_metrics or []:
        metrics.assert_emitted(name=metric_name, times=times)


@pytest.mark.parametrize(
    "test_context, update",
    [
        pytest.param(context, context.update_request, id=context.id)
        for context in handler_stop_for_accessing_meeting_not_owned_factory()
    ],
    indirect=["update"],
)
async def test_callback_fails_when_meeting_not_accessible(
    mock_session: MockDbSession,
    test_context: Context,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    metrics: MetricAssertions,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(create_meetup(id=MEETING_ID_NOT_OWNED))

    context, _ = await call_handler(
        test_context.handler_id, handler_context=handler_context, with_meeting_id=test_context.meeting_id
    )

    # MeetingNotOwned error metric is emitted
    metrics.assert_emitted(name=MetricKey.ERROR.with_prefix("MeetingNotOwned"), value=1)
    _assert_handler_metrics(metrics, fault_value=0, extra_metrics=test_context.extra_metrics)
    # The user is sent to the main menu
    context.api.assert_edit_message_called(update, factory.main_menu_view(lang=user_with_settings.lang))


@pytest.mark.parametrize(
    "test_context, update",
    [
        pytest.param(context, context.update_request, id=context.id)
        for context in handler_stop_for_accessing_meeting_not_owned_factory()
        if context.shows_deleted_message_when_not_found
    ],
    indirect=["update"],
)
async def test_callback_fails_when_meeting_not_found(
    mock_session: MockDbSession,
    test_context: Context,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    metrics: MetricAssertions,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, _ = await call_handler(
        test_context.handler_id, handler_context=handler_context, with_meeting_id=test_context.meeting_id
    )

    extra = (
        test_context.extra_metrics_not_found
        if not isinstance(test_context.extra_metrics_not_found, _Unset)
        else test_context.extra_metrics
    )
    _assert_handler_metrics(metrics, fault_value=0, extra_metrics=extra)

    keyboard = test_context.custom_keyboard or [
        [
            ButtonConfig(
                text=f"{ButtonMessages.MAIN_MENU.back(lang=user_with_settings.lang)}",
                callback_data=cb.MAIN_MENU,
            )
        ]
    ]
    context.api.assert_edit_message_called(
        update,
        MitupView(
            description=CommonMessages.DELETED_MEETING_ALERT.get(lang=user_with_settings.lang),
            keyboard=keyboard,
        ),
    )


@pytest.mark.parametrize(
    "test_context, update",
    [
        pytest.param(context, context.update_request, id=context.id)
        for context in handler_stops_due_to_malformed_callback_data()
    ],
    indirect=["update"],
)
async def test_callback_fails_with_malformed_callback_data(
    mock_session: MockDbSession,
    test_context: Context,
    update: Update,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    context, _ = await call_handler(
        test_context.handler_id, handler_context=handler_context, with_meeting_id=test_context.meeting_id
    )

    # emit_global=True for FAULT means 2 records (FAULT=1 for handler dims + global)
    metrics.assert_emitted(name=MetricKey.FAULT.with_prefix("MalformedCallbackData"), value=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=1, times=2)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=2)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=2)


@pytest.mark.parametrize(
    "test_context, update",
    [pytest.param(context, context.update_request, id=context.id) for context in handler_stops_when_user_not_found()],
    indirect=["update"],
)
async def test_callback_fails_when_user_is_not_found(
    mock_session: MockDbSession,
    test_context: Context,
    update: Update,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    # Do not register the user in the db and call the handler
    context, _ = await call_handler(
        test_context.handler_id, handler_context=handler_context, with_meeting_id=test_context.meeting_id
    )

    metrics.assert_emitted(name=MetricKey.FAULT.with_prefix("UserNotFound"), value=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=1, times=2)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=2)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=2)


@pytest.mark.parametrize(
    "test_context, update",
    [
        pytest.param(context, context.update_request, id=context.id)
        for context in handler_stops_due_to_missing_user_data()
    ],
    indirect=["update"],
)
async def test_callback_fails_when_missing_necessary_user_data(
    mock_session: MockDbSession,
    test_context: Context,
    update: Update,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    # If context data is needed it should be validated before having to hit the database.
    # The fault should happen before testing if any object exists in the db and, therefore,
    # there is no need to add any.
    # If this test fails because the an object is not found in the database, it means that the
    # validation is not happening in the right place and the callback needs to be updated.
    context, _ = await call_handler(
        test_context.handler_id, handler_context=handler_context, with_meeting_id=test_context.meeting_id
    )

    metrics.assert_emitted(name=MetricKey.FAULT.with_prefix("ContextPropertyNotSetError"), value=1)
    metrics.assert_emitted(name=MetricKey.FAULT, value=1, times=2)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=2)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=2)


@pytest.mark.parametrize(
    "test_context, update",
    [
        pytest.param(context, context.update_request, id=context.id)
        for context in handler_shows_reactivation_prompt_for_inactive_meeting()
    ],
    indirect=["update"],
)
async def test_owner_sees_reactivation_prompt_for_inactive_meeting(
    mock_session: MockDbSession,
    test_context: Context,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    metrics: MetricAssertions,
):
    inactive_meeting = create_meetup(id=MEETING_ID_INACTIVE, active=False)
    user_with_settings.meetups.append(inactive_meeting)
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(inactive_meeting)

    context, _ = await call_handler(
        test_context.handler_id, handler_context=handler_context, with_meeting_id=test_context.meeting_id
    )

    _assert_handler_metrics(metrics, fault_value=0, extra_metrics=test_context.extra_metrics)

    back_rows = (
        test_context.reactivation_back_keyboard_factory(user_with_settings.lang)
        if test_context.reactivation_back_keyboard_factory
        else None
    )
    context.api.assert_edit_message_called(
        update,
        factory.reactivation_prompt_view(
            lang=user_with_settings.lang, meeting_id=MEETING_ID_INACTIVE, back_rows=back_rows
        ),
    )


@pytest.mark.parametrize(
    "test_context, update",
    [
        pytest.param(context, context.update_request, id=context.id)
        for context in handler_shows_reactivation_prompt_for_inactive_meeting()
    ],
    indirect=["update"],
)
async def test_non_owner_sees_main_menu_for_inactive_meeting(
    mock_session: MockDbSession,
    test_context: Context,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    metrics: MetricAssertions,
):
    """Non-owner accessing an inactive meeting is redirected to main menu (not-owned behavior)."""
    inactive_meeting = create_meetup(id=MEETING_ID_INACTIVE, active=False)
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(inactive_meeting)

    context, _ = await call_handler(
        test_context.handler_id, handler_context=handler_context, with_meeting_id=test_context.meeting_id
    )

    extra = (
        test_context.extra_metrics_non_owner_inactive
        if not isinstance(test_context.extra_metrics_non_owner_inactive, _Unset)
        else test_context.extra_metrics
    )
    metrics.assert_emitted(name=MetricKey.ERROR.with_prefix("MeetingNotOwned"), value=1)
    _assert_handler_metrics(metrics, fault_value=0, extra_metrics=extra)
    context.api.assert_edit_message_called(update, factory.main_menu_view(lang=user_with_settings.lang))
