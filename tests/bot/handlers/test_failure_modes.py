"""
This module contains tests for common failure modes for all handlers. The intention is that we remove the need of
repeating the same kind of tests for every hanlder that should behave the same.

We just need to update the factory methods that produces the parameters for each test case.
"""

import datetime as dt
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

import pytest
from telegram import Location, MessageEntity, Update
from telegram.ext import ConversationHandler

from mitup_bot.custom_context import BOT_CONFIG_KEY, ContextId
from mitup_bot.exceptions import MalformedCallbackData
from mitup_bot.handler_id import HandlerId
from mitup_bot.handlers.admin.enums import AdminHandlerId
from mitup_bot.handlers.broadcast.enums import BroadcastHandlerId
from mitup_bot.handlers.collaborate.enums import CollaborateHandlerId
from mitup_bot.handlers.command_enums import CommandsId
from mitup_bot.handlers.edit_settings.enums import EditSettingsHandlerId
from mitup_bot.handlers.inline_query.enums import InlineQueryId
from mitup_bot.handlers.main_menu.enums import MainMenuHandlerId
from mitup_bot.handlers.meeting.edit.enums import EditMeetingHandlerId
from mitup_bot.handlers.meeting.enums import MeetingHandlerId
from mitup_bot.handlers.messages import MessagesId
from mitup_bot.handlers.privacy.enums import PrivacyHandlerId
from mitup_bot.handlers.registration_process.enums import RegistrationProcessHandlerId
from mitup_bot.handlers.stale_cancel import StaleCancelHandlerId
from mitup_bot.keyboards import ButtonConfig, Keyboard
from mitup_bot.models import User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricUnit
from mitup_bot.translations import TranslationEngine
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, CommonMessages, PrivacyMessages
from mitup_bot.views import MitupView, RenderContext, factory
from mitup_bot.views import meeting as meeting_views
from tests.helpers import (
    AnyFloat,
    HandlerContext,
    StubMitupContext,
    UpdateRequest,
    call_handler,
    create_bot_config,
    create_meetup,
    create_member,
    create_user,
)
from tests.helpers.constants import DEFAULT_USER_ID
from tests.helpers.logs import ACCOUNT_NOT_FOUND_LOG_EVENT
from tests.helpers.monitoring import MetricAssertions
from tests.helpers.stub_db import MockDbSession

MEETING_ID_NOT_OWNED = 99
MEETING_ID_NOT_FOUND = 9999
MEETING_ID_INACTIVE = 88


class _Unset:
    """Sentinel type for dataclass fields that distinguish 'not set' from None."""


UNSET: Final = _Unset()


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
    # Only declared explicitly for handlers that catch UserNotFound themselves (join/leave):
    # every USER_NOT_FOUND context already gets the pending-deletion test automatically, since
    # guards.current_user raises both exceptions from the same call site.
    USER_PENDING_DELETION = "UserPendingDeletion"


@dataclass
class Context:
    handler_id: HandlerId
    update_request: UpdateRequest
    id: str
    error_modes: set[ErrorMode]
    # Admin-gated handlers are dropped by the registry gate for non-admins before any guard runs;
    # the failure-mode tests stash a config making the acting user an admin so the guard under the
    # gate is actually reached.
    admin_only: bool = False
    user_fixture: str = "user_with_settings"
    exception: Exception | None = None
    fault_count: int = 0  # This is the value of the fault metric (both with and without prefix)
    custom_keyboard: Keyboard | None = None  # Used when the meeting does not exist and the message is edited
    reactivation_back_keyboard_factory: Callable[[str], Keyboard] | None = (
        None  # Lang-dependent back row for the reactivation prompt
    )
    # False for handlers whose guard runs with access=OWNER_ANY_STATE, where a meeting that no
    # longer resolves is answered as not-owned instead of with the deleted-meeting notice.
    shows_deleted_message_when_not_found: bool = True
    meeting_id: dict[ContextId, int] | None = None  # Meeting id to store in the context data
    # Extra metric emissions for this handler. Each is a (name, times) pair.
    extra_metrics: list[tuple[str, int]] = field(default_factory=list)
    # Override extra_metrics when the meeting is not found. Uses UNSET sentinel as default (falls back to
    # extra_metrics). Set to [] to explicitly assert no extra metrics.
    extra_metrics_not_found: list[tuple[str, int]] | None | _Unset = field(default_factory=_Unset)
    # Override extra_metrics for the non-owner inactive meeting test.
    extra_metrics_non_owner_inactive: list[tuple[str, int]] | None | _Unset = field(default_factory=_Unset)


def other_owner() -> User:
    """The owner of the meetings the acting user must not reach.

    The meeting guard resolves ownership on the loaded meeting (`Meetup.is_owned_by`), so a meeting
    that stands in for "somebody else's" needs a real owner.
    """
    return create_member(id=2, tg_user_id=DEFAULT_USER_ID + 1)


def make_admin_if_gated(handler_context: HandlerContext, test_context: Context):
    """Stash a config making the acting user an admin when the context's handler is admin-gated,
    so the registry gate lets the update through to the guard being exercised."""
    if test_context.admin_only:
        handler_context.app.bot_data[BOT_CONFIG_KEY] = create_bot_config([DEFAULT_USER_ID])


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
        meeting_id={ContextId.EDIT_MEETING_TIME: 99},
    ),
    Context(
        handler_id=EditMeetingHandlerId.WRONG_TIME_FORMAT,
        update_request=UpdateRequest(message_text="12:00"),
        error_modes={ErrorMode.USER_NOT_FOUND, ErrorMode.MEETING_NOT_OWNED},
        id="wrong_time_format",
        meeting_id={ContextId.EDIT_MEETING_TIME: 99},
    ),
    Context(
        handler_id=EditMeetingHandlerId.WRONG_TIME_FORMAT,
        update_request=UpdateRequest(message_text="12:00"),
        error_modes={ErrorMode.MISSING_USER_DATA},
        id="wrong_time_format",
    ),
    Context(
        handler_id=EditMeetingHandlerId.DATETIME_WRONG_TEXT_FORMAT,
        update_request=UpdateRequest(message_text="some text"),
        error_modes={ErrorMode.USER_NOT_FOUND, ErrorMode.MEETING_NOT_OWNED},
        id="datetime_wrong_text_format",
        meeting_id={ContextId.EDIT_MEETING_TIME: 99},
    ),
    Context(
        handler_id=EditMeetingHandlerId.DATETIME_WRONG_TEXT_FORMAT,
        update_request=UpdateRequest(message_text="some text"),
        error_modes={ErrorMode.MISSING_USER_DATA},
        id="datetime_wrong_text_format",
    ),
    Context(
        handler_id=EditMeetingHandlerId.DATETIME_WRONG_MESSAGE,
        update_request=UpdateRequest(location=Location(latitude=0, longitude=0)),
        error_modes={ErrorMode.USER_NOT_FOUND, ErrorMode.MEETING_NOT_OWNED},
        id="datetime_wrong_message",
        meeting_id={ContextId.EDIT_MEETING_TIME: 99},
    ),
    Context(
        handler_id=EditMeetingHandlerId.DATETIME_WRONG_MESSAGE,
        update_request=UpdateRequest(location=Location(latitude=0, longitude=0)),
        error_modes={ErrorMode.MISSING_USER_DATA},
        id="datetime_wrong_message",
    ),
    # DATE_TIME_ENTITY_MESSAGE — requires a message with a date_time entity and a stored meeting id
    Context(
        handler_id=EditMeetingHandlerId.DATE_TIME_ENTITY_MESSAGE,
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
        id="date_time_entity_message",
        meeting_id={ContextId.EDIT_MEETING_TIME: 99},
    ),
    Context(
        handler_id=EditMeetingHandlerId.DATE_TIME_ENTITY_MESSAGE,
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
        id="date_time_entity_message",
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
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="edit_meeting_settings",
    ),
    Context(
        handler_id=EditMeetingHandlerId.SET_MEETING_WAITING_LIST_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SET_MEETING_WAITING_LIST.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="set_meeting_waiting_list",
    ),
    Context(
        handler_id=EditMeetingHandlerId.SET_MEETING_PUBLIC_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SET_MEETING_PUBLIC.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="set_meeting_public",
    ),
    Context(
        handler_id=EditMeetingHandlerId.SET_MEETING_INCOGNITO_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SET_MEETING_INCOGNITO.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="set_meeting_incognito",
    ),
    Context(
        handler_id=EditMeetingHandlerId.SET_MEETING_ALLOW_INVITATIONS_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SET_MEETING_ALLOW_INVITATIONS.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="set_meeting_allow_invitations",
    ),
    Context(
        handler_id=EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_CALLBACK,
        update_request=UpdateRequest(
            callback_query=cb.EDIT_MEETING_KICK_OUT_PARTICIPANTS.with_ids(MEETING_ID_NOT_FOUND, 1)
        ),
        error_modes={ErrorMode.MEETING_NOT_FOUND, ErrorMode.USER_NOT_FOUND},
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
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="edit_meeting_participants_kick_out",
    ),
    Context(
        handler_id=EditMeetingHandlerId.PARTICIPANTS_KICK_OUT_ACTION_CONFIRM_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.CONFIRM_KICK_OUT.with_ids(MEETING_ID_NOT_OWNED, 1)),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
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
    # --- Inactive meeting accessed by owner (handlers guarded with the default access) ---
    Context(
        handler_id=MeetingHandlerId.SHOW_MEETING_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SHOW_MEETING.with_id(MEETING_ID_INACTIVE)),
        error_modes={ErrorMode.MEETING_INACTIVE_OWNER, ErrorMode.USER_NOT_FOUND},
        id="show_inactive_meeting",
        reactivation_back_keyboard_factory=lambda lang: [
            [
                ButtonConfig(
                    text=ButtonMessages.ACTIVE_MEETINGS.get_text(lang=lang),
                    callback_data=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(1),
                )
            ]
        ],
    ),
    Context(
        handler_id=MeetingHandlerId.DECLINE_DELETE_MEETING_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.DECLINE_DELETE_MEETING.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="decline_delete_meeting",
    ),
    Context(
        handler_id=MeetingHandlerId.DECLINE_DELETE_MEETING_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.DECLINE_DELETE_MEETING.with_id(MEETING_ID_INACTIVE)),
        error_modes={ErrorMode.MEETING_INACTIVE_OWNER},
        id="decline_delete_inactive_meeting",
    ),
    Context(
        handler_id=EditMeetingHandlerId.EDIT,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING.with_id(MEETING_ID_INACTIVE)),
        error_modes={ErrorMode.MEETING_INACTIVE_OWNER, ErrorMode.USER_NOT_FOUND},
        id="edit_inactive_meeting",
    ),
    Context(
        handler_id=EditMeetingHandlerId.TITLE_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_TITLE.with_id(MEETING_ID_INACTIVE)),
        error_modes={ErrorMode.MEETING_INACTIVE_OWNER, ErrorMode.USER_NOT_FOUND},
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
    # Duration end-datetime entry / back navigation (both keyed on EDIT_MEETING_END_DATE_TIME)
    Context(
        handler_id=EditMeetingHandlerId.DURATION_END_ENTRY_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_END_DATE_TIME.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="duration_end_entry",
    ),
    Context(
        handler_id=EditMeetingHandlerId.DURATION_END_ENTRY_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_END_DATE_TIME),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="duration_end_entry_malformed",
    ),
    Context(
        handler_id=EditMeetingHandlerId.DURATION_BACK_TO_END_DATETIME_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_END_DATE_TIME.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
        id="duration_back_to_end_datetime",
    ),
    Context(
        handler_id=EditMeetingHandlerId.DURATION_BACK_TO_END_DATETIME_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_END_DATE_TIME),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="duration_back_to_end_datetime_malformed",
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
        error_modes={ErrorMode.USER_NOT_FOUND, ErrorMode.MEETING_NOT_OWNED},
        id="duration_end_wrong_input",
        meeting_id={ContextId.EDIT_MEETING_END_DATETIME: 99},
    ),
    Context(
        handler_id=EditMeetingHandlerId.DURATION_END_WRONG_INPUT,
        update_request=UpdateRequest(message_text="some text"),
        error_modes={ErrorMode.MISSING_USER_DATA},
        id="duration_end_wrong_input",
    ),
    Context(
        handler_id=EditMeetingHandlerId.DURATION_END_TIME_WRONG_INPUT,
        update_request=UpdateRequest(message_text="bad time"),
        error_modes={ErrorMode.USER_NOT_FOUND, ErrorMode.MEETING_NOT_OWNED},
        id="duration_end_time_wrong_input",
        meeting_id={ContextId.EDIT_MEETING_END_DATETIME: 99},
    ),
    Context(
        handler_id=EditMeetingHandlerId.DURATION_END_TIME_WRONG_INPUT,
        update_request=UpdateRequest(message_text="bad time"),
        error_modes={ErrorMode.MISSING_USER_DATA},
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
    # --- Documented exclusions ---
    #
    # Handlers that serve a caller with no account row instead of stopping on one, so no ErrorMode
    # applies to them. Leaving USER_NOT_FOUND off a handler is a claim, not an omission:
    # `test_handler_does_not_reject_a_caller_with_no_account` runs every handler listed here with no
    # user row and asserts the rejection was never recorded, so a handler that starts requiring an
    # account fails there instead of quietly losing its coverage. The notes below say why each one
    # belongs.
    #   - CommandsId.START_WITH_EXISTING_USER: gates on guards.member_user, which returns None
    #     instead of raising — an unknown user gets a silent END with no fault
    #     (guards.current_user only runs after the MEMBER check succeeded, so its UserNotFound
    #     path is unreachable). The group -1 / group 0 routing is covered end-to-end in
    #     test_start_routing.py.
    #   - RegistrationProcessHandlerId.TIMEZONE_COMMAND: gates on guards.member_user; an unknown
    #     user is the normal onboarding case — the handler creates the row and claims the update
    #     via ApplicationHandlerStop rather than failing. Covered in test_start_routing.py and
    #     test_commands.py.
    #   - MeetingHandlerId.JOIN / MeetingHandlerId.LEAVE: catch UserNotFound themselves and
    #     register a default JOINED_ONLY user instead — an unregistered user pressing the button
    #     is a valid case, covered in tests/handlers/meeting/test_join_leave_meeting.py. They DO
    #     declare USER_PENDING_DELETION below: the rejection escapes their UserNotFound catch.
    #   - MeetingHandlerId.INVITE_USERS_CALLBACK: uses guards.user_registered, which answers the
    #     callback query with an alert instead of raising when the user is unregistered.
    #   - InlineQueryId.SHARE_MEETING: a public meeting card carries a Share button for every reader
    #     of the chat, so the sharer resolves optionally and an unregistered one is a valid case that
    #     reaches guards.meeting with no user. A user marked for deletion is collapsed to the same
    #     anonymous caller instead of raising, so neither USER_NOT_FOUND nor USER_PENDING_DELETION
    #     applies. Both are covered in tests/bot/handlers/inline_query/test_share_meeting.py.
    #   - MeetingHandlerId.ATTACH_TO_CHAT: the "Make it searchable" button renders on every card the
    #     meeting was shared into, so the caller resolves optionally and an unregistered one is a
    #     valid case that reaches guards.shared_meeting with no user. A user marked for deletion is
    #     collapsed to the same anonymous caller instead of raising, so neither USER_NOT_FOUND nor
    #     USER_PENDING_DELETION applies. Both are covered in
    #     tests/bot/handlers/meeting/test_attach_to_chat.py. It still declares
    #     MALFORMED_CALLBACK_DATA below, which the guard raises before the caller is resolved.
    #
    # --- /start and registration-process handlers ---
    Context(
        handler_id=CommandsId.MAIN_MENU,
        update_request=UpdateRequest(command="main_menu"),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="command_main_menu",
    ),
    # The conversation-state handlers below reach guards.current_user before their claim_update
    # wrapper can raise ApplicationHandlerStop, so the standard UserNotFound rejection applies.
    Context(
        handler_id=RegistrationProcessHandlerId.TIMEZONE_MESSAGE_WITH_TEXT,
        update_request=UpdateRequest(message_text="Madrid"),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="registration_timezone_text",
    ),
    Context(
        handler_id=RegistrationProcessHandlerId.TIMEZONE_MESSAGE_WITH_LOCATION,
        update_request=UpdateRequest(location=Location(latitude=0, longitude=0)),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="registration_timezone_location",
    ),
    Context(
        handler_id=RegistrationProcessHandlerId.TIMEZONE_INVALID_INPUT,
        update_request=UpdateRequest(command="start"),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="registration_timezone_invalid_input",
    ),
    # --- Global message fallback ---
    Context(
        handler_id=MessagesId.MESSAGE_WITHOUT_TEXT,
        update_request=UpdateRequest(location=Location(latitude=0, longitude=0)),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="message_without_text",
    ),
    Context(
        handler_id=MessagesId.MESSAGE_RICH,
        update_request=UpdateRequest(rich_message=True),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="rich_message",
    ),
    # --- Per-conversation rich message fallbacks ---
    Context(
        handler_id=MeetingHandlerId.CREATE_MEETING_RICH_MESSAGE,
        update_request=UpdateRequest(rich_message=True),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="create_meeting_rich_message",
    ),
    Context(
        handler_id=EditMeetingHandlerId.TITLE_RICH_MESSAGE,
        update_request=UpdateRequest(rich_message=True),
        error_modes={ErrorMode.USER_NOT_FOUND, ErrorMode.MEETING_NOT_OWNED},
        id="edit_title_rich_message",
        meeting_id={ContextId.EDIT_MEETING_TITLE: MEETING_ID_NOT_OWNED},
    ),
    Context(
        handler_id=EditMeetingHandlerId.DESCRIPTION_RICH_MESSAGE,
        update_request=UpdateRequest(rich_message=True),
        error_modes={ErrorMode.USER_NOT_FOUND, ErrorMode.MEETING_NOT_OWNED},
        id="edit_description_rich_message",
        meeting_id={ContextId.EDIT_MEETING_DESCRIPTION: MEETING_ID_NOT_OWNED},
    ),
    Context(
        handler_id=EditMeetingHandlerId.LOCATION_NAME_RICH_MESSAGE,
        update_request=UpdateRequest(rich_message=True),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="edit_location_name_rich_message",
    ),
    Context(
        handler_id=EditSettingsHandlerId.TIMEZONE_RICH_MESSAGE,
        update_request=UpdateRequest(rich_message=True),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="settings_timezone_rich_message",
    ),
    # --- Main menu handlers ---
    Context(
        handler_id=MainMenuHandlerId.MAIN_MENU_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.MAIN_MENU),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="main_menu",
    ),
    Context(
        handler_id=MainMenuHandlerId.SEND_MAIN_MENU_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SEND_MAIN_MENU),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="send_main_menu",
    ),
    Context(
        handler_id=MainMenuHandlerId.SHOW_MEETINGS_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(1)),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="show_active_meetings",
    ),
    Context(
        handler_id=MainMenuHandlerId.SHOW_JOINED_MEETINGS_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SHOW_JOINED_MEETINGS_PAGE.with_id(1)),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="show_joined_meetings",
    ),
    Context(
        handler_id=MainMenuHandlerId.SHOW_HELP_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.HELP),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="show_help",
    ),
    # --- Collaborate handlers ---
    Context(
        handler_id=CollaborateHandlerId.SHOW,
        update_request=UpdateRequest(callback_query=cb.COLLABORATE),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="collaborate_show",
    ),
    Context(
        handler_id=CollaborateHandlerId.UNLINK,
        update_request=UpdateRequest(callback_query=cb.UNLINK_PATREON),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="collaborate_unlink",
    ),
    Context(
        handler_id=CollaborateHandlerId.UNLINK_CONFIRM,
        update_request=UpdateRequest(callback_query=cb.CONFIRM_PATREON_UNLINK),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="collaborate_unlink_confirm",
    ),
    Context(
        handler_id=CollaborateHandlerId.UNLINK_DECLINE,
        update_request=UpdateRequest(callback_query=cb.DECLINE_PATREON_UNLINK),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="collaborate_unlink_decline",
    ),
    Context(
        handler_id=CollaborateHandlerId.PATREON_LINK_CONFIRM,
        update_request=UpdateRequest(callback_query=cb.CONFIRM_PATREON_LINK.with_code("a-code")),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="collaborate_patreon_link_confirm",
    ),
    Context(
        # A confirm button whose code is missing: the guard rejects it rather than looking up a row
        # there is no address for.
        handler_id=CollaborateHandlerId.PATREON_LINK_CONFIRM,
        update_request=UpdateRequest(callback_query=cb.CONFIRM_PATREON_LINK),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        id="collaborate_patreon_link_confirm_no_code",
    ),
    Context(
        handler_id=CollaborateHandlerId.PATREON_LINK_DECLINE,
        update_request=UpdateRequest(callback_query=cb.DECLINE_PATREON_LINK.with_code("a-code")),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="collaborate_patreon_link_decline",
    ),
    # --- Privacy handlers ---
    Context(
        handler_id=PrivacyHandlerId.SHOW,
        update_request=UpdateRequest(callback_query=cb.EDIT_PRIVACY),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="privacy_show",
    ),
    Context(
        handler_id=PrivacyHandlerId.SEND_PRIVACY,
        update_request=UpdateRequest(callback_query=cb.SEND_PRIVACY),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="privacy_send_privacy",
    ),
    Context(
        handler_id=PrivacyHandlerId.EXPORT_DATA,
        update_request=UpdateRequest(callback_query=cb.EXPORT_USER_DATA),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="privacy_export_data",
    ),
    Context(
        handler_id=PrivacyHandlerId.DELETE_DATA,
        update_request=UpdateRequest(callback_query=cb.DELETE_USER_DATA),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="privacy_delete_data",
    ),
    Context(
        handler_id=PrivacyHandlerId.CONFIRM_DELETE_DATA,
        update_request=UpdateRequest(callback_query=cb.CONFIRM_DELETE_USER_DATA),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="privacy_confirm_delete_data",
    ),
    Context(
        handler_id=PrivacyHandlerId.CONFIRM_DELETE_DATA_FINAL,
        update_request=UpdateRequest(callback_query=cb.CONFIRM_DELETE_USER_DATA_FINAL),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="privacy_confirm_delete_data_final",
    ),
    Context(
        handler_id=PrivacyHandlerId.DECLINE_DELETE_DATA,
        update_request=UpdateRequest(callback_query=cb.DECLINE_DELETE_USER_DATA),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="privacy_decline_delete_data",
    ),
    # Join/leave catch UserNotFound (unregistered users are a valid case) but the pending-deletion
    # rejection escapes that catch, so they declare the mode explicitly.
    Context(
        handler_id=MeetingHandlerId.JOIN,
        update_request=UpdateRequest(callback_query=cb.JOIN.with_id(1)),
        error_modes={ErrorMode.USER_PENDING_DELETION},
        id="join_meeting_pending_deletion",
    ),
    Context(
        handler_id=MeetingHandlerId.LEAVE,
        update_request=UpdateRequest(callback_query=cb.LEAVE.with_id(1)),
        error_modes={ErrorMode.USER_PENDING_DELETION},
        id="leave_meeting_pending_deletion",
    ),
    # --- Settings handlers ---
    Context(
        handler_id=EditSettingsHandlerId.EDIT,
        update_request=UpdateRequest(callback_query=cb.SETTINGS),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="settings_entry",
    ),
    Context(
        handler_id=EditSettingsHandlerId.CANCEL,
        update_request=UpdateRequest(callback_query=cb.CANCEL_SETTINGS),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="settings_cancel",
    ),
    Context(
        handler_id=EditSettingsHandlerId.NOTIFICATIONS_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_NOTIFICATIONS),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="settings_notifications",
    ),
    Context(
        handler_id=EditSettingsHandlerId.TOGGLE_NOTIFICATIONS,
        update_request=UpdateRequest(callback_query=cb.TOGGLE_NOTIFICATIONS),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="settings_toggle_notifications",
    ),
    Context(
        handler_id=EditSettingsHandlerId.SET_NOTIFICATION_TIME,
        update_request=UpdateRequest(callback_query=cb.SET_NOTIFICATION_TIME),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="settings_set_notification_time",
    ),
    Context(
        handler_id=EditSettingsHandlerId.NOTIFICATION_TIME_MESSAGE_WITH_TEXT,
        update_request=UpdateRequest(message_text="30"),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="settings_notification_time_text",
    ),
    Context(
        handler_id=EditSettingsHandlerId.NOTIFICATION_TIME_INVALID_INPUT,
        update_request=UpdateRequest(message_text="not a number"),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="settings_notification_time_invalid",
    ),
    Context(
        handler_id=EditSettingsHandlerId.TIMEZONE_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_TIEMZONE),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="settings_timezone_entry",
    ),
    Context(
        handler_id=EditSettingsHandlerId.TIMEZONE_MESSAGE_WITH_TEXT,
        update_request=UpdateRequest(message_text="Madrid"),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="settings_timezone_text",
    ),
    Context(
        handler_id=EditSettingsHandlerId.TIMEZONE_MESSAGE_WITH_LOCATION,
        update_request=UpdateRequest(location=Location(latitude=0, longitude=0)),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="settings_timezone_location",
    ),
    Context(
        handler_id=EditSettingsHandlerId.TIMEOUT_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_TIMEOUT),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="settings_timeout_entry",
    ),
    Context(
        handler_id=EditSettingsHandlerId.TIMEOUT_MESSAGE_WITH_TEXT,
        update_request=UpdateRequest(message_text="30"),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="settings_timeout_text",
    ),
    Context(
        handler_id=EditSettingsHandlerId.TIMEOUT_INVALID_INPUT,
        update_request=UpdateRequest(message_text="not a number"),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="settings_timeout_invalid",
    ),
    Context(
        handler_id=EditSettingsHandlerId.SET_LANGUAGE_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.SET_LANGUAGE.with_id(0)),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="settings_set_language",
    ),
    # --- Meeting lifecycle handlers ---
    Context(
        handler_id=MeetingHandlerId.CREATE_MEETING_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.CREATE_MEETING),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="create_meeting_entry",
    ),
    Context(
        handler_id=MeetingHandlerId.DELETE_MEETING_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.DELETE_MEETING.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="delete_meeting",
    ),
    Context(
        handler_id=MeetingHandlerId.CONFIRM_DELETE_MEETING_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.CONFIRM_DELETE_MEETING.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="confirm_delete_meeting",
    ),
    # --- Invite flow handlers ---
    Context(
        handler_id=MeetingHandlerId.INVITE_USERS_CANCEL_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.CANCEL_INVITE_USER.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="invite_users_cancel",
    ),
    Context(
        handler_id=MeetingHandlerId.INVITE_USERS_DECLINE_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.CANCEL_INVITE_USER.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="invite_users_decline",
    ),
    Context(
        handler_id=MeetingHandlerId.INVITE_USERS_NAME_MESSAGE,
        update_request=UpdateRequest(message_text="John Doe"),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="invite_users_name_message",
    ),
    Context(
        handler_id=MeetingHandlerId.INVITE_USERS_CONFIRM_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.CONFIRM_INVITE_USER.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="invite_users_confirm",
    ),
    Context(
        handler_id=MeetingHandlerId.INVITE_USERS_FALLBACK,
        update_request=UpdateRequest(callback_query=True),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="invite_users_fallback",
    ),
    # --- Edit meeting handlers ---
    Context(
        handler_id=EditMeetingHandlerId.CANCEL,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_CANCEL.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="edit_meeting_cancel",
    ),
    Context(
        handler_id=EditMeetingHandlerId.DESCRIPTION_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_DESCRIPTION.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="edit_meeting_description",
    ),
    Context(
        handler_id=EditMeetingHandlerId.WRONG_TIME_MESSAGE,
        update_request=UpdateRequest(location=Location(latitude=0, longitude=0)),
        error_modes={ErrorMode.USER_NOT_FOUND, ErrorMode.MEETING_NOT_OWNED},
        id="wrong_time_message",
        meeting_id={ContextId.EDIT_MEETING_TIME: 99},
    ),
    Context(
        handler_id=EditMeetingHandlerId.WRONG_TIME_MESSAGE,
        update_request=UpdateRequest(location=Location(latitude=0, longitude=0)),
        error_modes={ErrorMode.MISSING_USER_DATA},
        id="wrong_time_message",
    ),
    # --- Inline query ---
    # Registered-sharer coverage only: the unregistered sharer is a valid case here, not a fault
    # (see the documented exclusions above).
    Context(
        handler_id=InlineQueryId.SHARE_MEETING,
        update_request=UpdateRequest(inline_query=str(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.MEETING_NOT_OWNED},
        id="inline_share_meeting",
    ),
    # --- Edit meeting title and description content steps ---
    Context(
        handler_id=EditMeetingHandlerId.TITLE_MESSAGE,
        update_request=UpdateRequest(message_text="A new title"),
        error_modes={ErrorMode.USER_NOT_FOUND, ErrorMode.MEETING_NOT_OWNED},
        id="edit_meeting_title_message",
        meeting_id={ContextId.EDIT_MEETING_TITLE: MEETING_ID_NOT_OWNED},
    ),
    Context(
        handler_id=EditMeetingHandlerId.DESCRIPTION_MESSAGE,
        update_request=UpdateRequest(message_text="A new description"),
        error_modes={ErrorMode.USER_NOT_FOUND, ErrorMode.MEETING_NOT_OWNED},
        id="edit_meeting_description_message",
        meeting_id={ContextId.EDIT_MEETING_DESCRIPTION: MEETING_ID_NOT_OWNED},
    ),
    # --- Edit meeting location flow ---
    Context(
        handler_id=EditMeetingHandlerId.LOCATION_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_LOCATION.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="edit_meeting_location",
    ),
    Context(
        handler_id=EditMeetingHandlerId.LOCATION_NAME_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_LOCATION_NAME.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="edit_meeting_location_name",
    ),
    Context(
        handler_id=EditMeetingHandlerId.LOCATION_CANCEL_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.CANCEL_EDIT_MEETING_LOCATION.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="edit_meeting_location_cancel",
    ),
    Context(
        handler_id=EditMeetingHandlerId.LOCATION_COORDINATES_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_LOCATION_COORDINATES.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="edit_meeting_location_coordinates",
    ),
    Context(
        handler_id=EditMeetingHandlerId.LOCATION_NAME_MESSAGE,
        update_request=UpdateRequest(message_text="Some place"),
        error_modes={ErrorMode.USER_NOT_FOUND, ErrorMode.MEETING_NOT_OWNED},
        id="edit_meeting_location_name_message",
        meeting_id={ContextId.EDIT_MEETING_LOCATION_NAME: MEETING_ID_NOT_OWNED},
        shows_deleted_message_when_not_found=False,
    ),
    Context(
        handler_id=EditMeetingHandlerId.LOCATION_COORDINATES_MESSAGE,
        update_request=UpdateRequest(location=Location(latitude=0, longitude=0)),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="edit_meeting_location_coordinates_message",
    ),
    Context(
        handler_id=EditMeetingHandlerId.LOCATION_COORDINATES_WRONG_MESSAGE,
        update_request=UpdateRequest(message_text="not a location"),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="edit_meeting_location_coordinates_wrong_message",
    ),
    # --- Edit meeting participants flow ---
    Context(
        handler_id=EditMeetingHandlerId.PARTICIPANTS_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_PARTICIPANTS.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="edit_meeting_participants",
    ),
    Context(
        handler_id=EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_MAX_PARTICIPANTS.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="edit_meeting_max_participants",
    ),
    Context(
        handler_id=EditMeetingHandlerId.PARTICIPANTS_NO_LIMIT_CALLBACK,
        update_request=UpdateRequest(
            callback_query=cb.EDIT_MEETING_NO_LIMIT_PARTICIPANTS.with_id(MEETING_ID_NOT_OWNED)
        ),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="edit_meeting_no_limit_participants",
    ),
    Context(
        handler_id=EditMeetingHandlerId.PARTICIPANTS_CANCEL_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.CANCEL_EDIT_MEETING_PARTICIPANS.with_id(MEETING_ID_NOT_OWNED)),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="edit_meeting_participants_cancel",
    ),
    Context(
        handler_id=EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_MESSAGE,
        update_request=UpdateRequest(message_text="5"),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="edit_meeting_max_participants_message",
    ),
    Context(
        handler_id=EditMeetingHandlerId.PARTICIPANTS_MAXIMUM_WRONG_MESSAGE,
        update_request=UpdateRequest(message_text="not a number"),
        error_modes={ErrorMode.USER_NOT_FOUND},
        id="edit_meeting_max_participants_wrong_message",
    ),
    # --- Admin menu handler ---
    # Admin-gated; stashes an admin config to reach guards.current_user under the gate.
    Context(
        handler_id=AdminHandlerId.ADMIN_MENU_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.ADMIN_MENU),
        error_modes={ErrorMode.USER_NOT_FOUND},
        admin_only=True,
        id="admin_menu",
    ),
    # --- Broadcast authoring handlers ---
    # All broadcast handlers are admin-gated (admin_only=True), so these contexts stash an admin
    # config to reach the guard under the gate. The content/invalid-content handlers gate on
    # guards.member_user via load_operator (returns None → silent AWAITING_CONTENT), so they never
    # raise UserNotFound and are covered by their own handler tests. The cancel handler tolerates a
    # missing callback id by design (the entry-prompt Cancel button carries none), so it has no
    # MalformedCallbackData mode.
    Context(
        handler_id=BroadcastHandlerId.BROADCAST_CONFIRM_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.CONFIRM_BROADCAST.with_id(1)),
        error_modes={ErrorMode.USER_NOT_FOUND},
        admin_only=True,
        id="broadcast_confirm",
    ),
    Context(
        handler_id=BroadcastHandlerId.BROADCAST_CONFIRM_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.CONFIRM_BROADCAST),
        error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
        admin_only=True,
        id="broadcast_confirm_malformed",
    ),
    Context(
        handler_id=BroadcastHandlerId.BROADCAST_CANCEL_CALLBACK,
        update_request=UpdateRequest(callback_query=cb.CANCEL_BROADCAST.with_id(1)),
        error_modes={ErrorMode.USER_NOT_FOUND},
        admin_only=True,
        id="broadcast_cancel",
    ),
]


# -------------------
# Factory methods
# -------------------
def handler_stop_for_accessing_meeting_not_owned_factory() -> list[Context]:
    return [context for context in CONTEXTS if ErrorMode.MEETING_NOT_OWNED in context.error_modes]


def handler_tolerates_unregistered_user() -> list[Context]:
    """One context per handler that declares `USER_NOT_FOUND` in none of its contexts.

    The complement of `handler_stops_when_user_not_found`, so the two together cover the registry:
    omitting the mode has to mean the handler serves a caller with no account, not that nobody
    checked. Without this pair a handler that starts turning a missing user away is caught only if
    somebody remembers to declare the mode, which is the same as not being caught.

    Grouped by handler rather than taken per context, because a handler carries several contexts —
    the inactive-meeting and not-owned variants among them — and the acting user is the same in all
    of them. One context per handler is enough to withhold that user, and picking the first keeps
    the parametrization stable as variants are added.
    """
    declares_it = {context.handler_id for context in CONTEXTS if ErrorMode.USER_NOT_FOUND in context.error_modes}
    first_per_handler: dict[HandlerId, Context] = {}
    for context in CONTEXTS:
        if context.handler_id not in declares_it:
            first_per_handler.setdefault(context.handler_id, context)
    return list(first_per_handler.values())


def handler_stops_when_user_not_found() -> list[Context]:
    return [context for context in CONTEXTS if ErrorMode.USER_NOT_FOUND in context.error_modes]


def handler_stops_when_meeting_not_found() -> list[Context]:
    return [context for context in CONTEXTS if ErrorMode.MEETING_NOT_FOUND in context.error_modes]


def handler_rejects_user_pending_deletion() -> list[Context]:
    """Every USER_NOT_FOUND context plus the explicit USER_PENDING_DELETION ones.

    UserPendingDeletion is raised by the same `guards.current_user` call that raises UserNotFound,
    so USER_NOT_FOUND membership automatically implies pending-deletion coverage; the explicit mode
    exists for handlers that catch UserNotFound themselves but let the rejection escape.
    """
    return [
        context
        for context in CONTEXTS
        if context.error_modes & {ErrorMode.USER_NOT_FOUND, ErrorMode.USER_PENDING_DELETION}
    ]


def handler_stops_due_to_missing_user_data() -> list[Context]:
    return [context for context in CONTEXTS if ErrorMode.MISSING_USER_DATA in context.error_modes]


def handler_stops_due_to_malformed_callback_data() -> list[Context]:
    return [context for context in CONTEXTS if ErrorMode.MALFORMED_CALLBACK_DATA in context.error_modes]


def handler_shows_reactivation_prompt_for_inactive_meeting() -> list[Context]:
    return [context for context in CONTEXTS if ErrorMode.MEETING_INACTIVE_OWNER in context.error_modes]


def assert_rejection_screen(context: StubMitupContext, update: Update, view: MitupView, *, lang: str):
    """Assert the rejection screen reached the user in the shape this update can carry.

    A callback query replaces the screen the button sits on; a message update has no message of
    ours to replace, so the rejection arrives as a fresh reply. An inline query can only carry
    results and answers every rejection with the same unavailable card, so `view` does not apply
    there and `lang` is what picks the screen.
    """
    if update.inline_query is not None:
        context.api.assert_answer_inline_query_called(
            update, results=[meeting_views.unavailable_inline_view(lang)], cache_time=0
        )
        return

    if update.callback_query is not None:
        context.api.assert_edit_message_called(update, view)
        context.api.assert_send_message_not_called()
    else:
        context.api.assert_send_message_called(update, view)
        context.api.assert_edit_message_not_called()


def assert_handler_metrics(
    metrics: MetricAssertions,
    *,
    fault_value: int,
    extra_metrics: list[tuple[str, int]] | None = None,
):
    """Assert the standard handler metrics emitted by callback_with_metrics."""
    # Each is a single dimensionless record — handler identity rides as an EMF property, so there
    # is no separate per-handler-dimensioned copy (issue #205).
    metrics.assert_emitted(name=MetricKey.FAULT, value=fault_value, times=1)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=1)
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
async def test_handler_rejects_meeting_not_owned(
    mock_session: MockDbSession,
    test_context: Context,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    metrics: MetricAssertions,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(create_meetup(id=MEETING_ID_NOT_OWNED, owner=other_owner()))

    context, result = await call_handler(
        test_context.handler_id, handler_context=handler_context, with_meeting_id=test_context.meeting_id
    )

    # The rejection aborts the handler and the user is shown a screen that navigates elsewhere,
    # so no flow survives the update: the handler ends any conversation it was part of.
    assert result == ConversationHandler.END
    # MeetingNotOwned error metric is emitted
    metrics.assert_emitted(name=MetricKey.MEETING_NOT_OWNED, value=1)
    assert_handler_metrics(metrics, fault_value=0, extra_metrics=test_context.extra_metrics)
    # The user is sent to the main menu
    assert_rejection_screen(
        context,
        update,
        factory.main_menu_view(RenderContext(lang=user_with_settings.lang)),
        lang=user_with_settings.lang,
    )


@pytest.mark.parametrize(
    "test_context, update",
    [
        pytest.param(context, context.update_request, id=context.id)
        for context in handler_stop_for_accessing_meeting_not_owned_factory()
        if context.shows_deleted_message_when_not_found
    ],
    indirect=["update"],
)
async def test_handler_rejects_meeting_that_is_gone(
    mock_session: MockDbSession,
    test_context: Context,
    update: Update,
    handler_context: HandlerContext,
    user_with_settings: User,
    metrics: MetricAssertions,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    context, result = await call_handler(
        test_context.handler_id, handler_context=handler_context, with_meeting_id=test_context.meeting_id
    )

    assert result == ConversationHandler.END
    extra = (
        test_context.extra_metrics_not_found
        if not isinstance(test_context.extra_metrics_not_found, _Unset)
        else test_context.extra_metrics
    )
    assert_handler_metrics(metrics, fault_value=0, extra_metrics=extra)

    keyboard = test_context.custom_keyboard or [
        [
            ButtonConfig(
                text=f"{ButtonMessages.MAIN_MENU.back(lang=user_with_settings.lang)}",
                callback_data=cb.MAIN_MENU,
            )
        ]
    ]
    assert_rejection_screen(
        context,
        update,
        MitupView(
            description=CommonMessages.DELETED_MEETING_ALERT.get(lang=user_with_settings.lang),
            keyboard=keyboard,
        ),
        lang=user_with_settings.lang,
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
    make_admin_if_gated(handler_context, test_context)
    context, _ = await call_handler(
        test_context.handler_id, handler_context=handler_context, with_meeting_id=test_context.meeting_id
    )

    # Dimensionless handler metrics — a single record each (issue #205). The exception class rides
    # on the aggregate fault as the `error_type` property.
    metrics.assert_emitted(name=MetricKey.FAULT, value=1, times=1, exception=MalformedCallbackData)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=1)


@pytest.mark.parametrize(
    "test_context, update",
    [pytest.param(context, context.update_request, id=context.id) for context in handler_stops_when_user_not_found()],
    indirect=["update"],
)
async def test_callback_answers_a_caller_with_no_account(
    mock_session: MockDbSession,
    test_context: Context,
    update: Update,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """A caller with no account row is told how to get one, and the invocation closes as handled.

    Every context registered here drives the handler from the bot's own chat, which is the one
    surface where a missing account is an expected business state: an account that no longer exists
    is the normal end of the deletion and cleanup runs, so the interaction must stay in the `Fault`
    denominator at value 0 rather than page as a code fault. A context on any other surface would
    fail this assertion, which is the intended signal — there it is a fault, because that surface's
    own `guards.user_registered` was supposed to answer the caller first.

    The stored language went with the row, so the notice renders in the client's language, which
    these updates leave unset.
    """
    # Do not register the user in the db and call the handler
    make_admin_if_gated(handler_context, test_context)
    context, _ = await call_handler(
        test_context.handler_id, handler_context=handler_context, with_meeting_id=test_context.meeting_id
    )

    metrics.assert_emitted(name=MetricKey.FAULT, value=0, times=1)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=1)

    notice = CommonMessages.ACCOUNT_NOT_FOUND.get(lang=TranslationEngine.FALLBACK_LANG)
    if update.callback_query is not None:
        # The tapped screen is replaced by the notice alone: every button on it resolves through the
        # missing row and would be answered with this same notice.
        context.api.assert_edit_message_called(update, notice)
        context.api.assert_send_message_not_called()
    else:
        context.api.assert_send_message_called(update, notice)


@pytest.mark.parametrize(
    "test_context, update",
    [pytest.param(context, context.update_request, id=context.id) for context in handler_tolerates_unregistered_user()],
    indirect=["update"],
)
async def test_handler_does_not_reject_a_caller_with_no_account(
    mock_session: MockDbSession,
    test_context: Context,
    update: Update,
    handler_context: HandlerContext,
    caplog: pytest.LogCaptureFixture,
):
    """A context that does not declare `USER_NOT_FOUND` must genuinely tolerate a caller with no row.

    The rejection is read off the log rather than off the metric plane: the error handler closes a
    missing account as a handled outcome, so its `Fault` record is indistinguishable from the one a
    handler that served the caller normally produces. `handle_user_not_found` writes this line
    before any delivery, whatever shape the answer takes and whatever language it renders in, so it
    is the one signal that separates the two.
    """
    make_admin_if_gated(handler_context, test_context)
    with caplog.at_level(logging.WARNING):
        await call_handler(
            test_context.handler_id, handler_context=handler_context, with_meeting_id=test_context.meeting_id
        )

    rejections = [record for record in caplog.records if record.msg == ACCOUNT_NOT_FOUND_LOG_EVENT]
    assert not rejections, f"{test_context.handler_id} rejected a caller it is expected to serve"


@pytest.mark.parametrize(
    "test_context, update",
    [
        pytest.param(context, context.update_request, id=context.id)
        for context in handler_rejects_user_pending_deletion()
    ],
    indirect=["update"],
)
async def test_handler_rejects_user_pending_deletion(
    mock_session: MockDbSession,
    test_context: Context,
    update: Update,
    handler_context: HandlerContext,
    metrics: MetricAssertions,
):
    """A user marked for deletion is rejected with the standardized alert and no fault is emitted."""
    marked_user = create_user(id=1, tg_user_id=DEFAULT_USER_ID, status=UserStatus.DELETION_REQUESTED)
    mock_session.add_object(marked_user, "tg_user_id")
    make_admin_if_gated(handler_context, test_context)

    context, _ = await call_handler(
        test_context.handler_id, handler_context=handler_context, with_meeting_id=test_context.meeting_id
    )

    # An expected business state, not a fault: the dedicated error-handler branch answers the
    # interaction before any fault metric is emitted.
    metrics.assert_not_emitted(name=MetricKey.FAULT, value=1)
    if update.inline_query is not None:
        # An inline query can only be answered with results, and a dying account surfaces none.
        context.api.assert_answer_inline_query_called(update, results=[], cache_time=0)
        context.api.assert_send_message_not_called()
    elif update.callback_query is not None:
        context.api.assert_answer_callback_query_called(
            update, text=PrivacyMessages.PENDING_DELETION_ALERT.get_text(lang=marked_user.lang), show_alert=True
        )
        context.api.assert_send_message_not_called()
    else:
        context.api.assert_send_message_called(
            update, PrivacyMessages.PENDING_DELETION_ALERT.get(lang=marked_user.lang)
        )


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
    # Missing context data is validated before the handler touches the database, so no object needs
    # seeding here. A miss is an expected consequence of in-memory conversation state, not a code
    # fault: the global error handler reclassifies it to the dedicated CONTEXT_LOST metric and the
    # fault series stays silent. If this test fails because an object is not found in the database,
    # the validation is not happening in the right place and the callback needs to be updated.
    context, _ = await call_handler(
        test_context.handler_id, handler_context=handler_context, with_meeting_id=test_context.meeting_id
    )

    metrics.assert_emitted(name=MetricKey.CONTEXT_LOST, value=1, times=1)
    metrics.assert_not_emitted(name=MetricKey.FAULT, value=1)
    metrics.assert_emitted(name=MetricKey.TIME, value=AnyFloat(), unit=MetricUnit.MILLISECONDS, times=1)
    metrics.assert_emitted(name=MetricKey.DB_CONNECTIONS_LEAKED, value=0, times=1)


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

    context, result = await call_handler(
        test_context.handler_id, handler_context=handler_context, with_meeting_id=test_context.meeting_id
    )

    assert result == ConversationHandler.END
    assert_handler_metrics(metrics, fault_value=0, extra_metrics=test_context.extra_metrics)

    back_rows = (
        test_context.reactivation_back_keyboard_factory(user_with_settings.lang)
        if test_context.reactivation_back_keyboard_factory
        else None
    )
    assert_rejection_screen(
        context,
        update,
        factory.reactivation_prompt_view(
            RenderContext(lang=user_with_settings.lang), meeting_id=MEETING_ID_INACTIVE, back_rows=back_rows
        ),
        lang=user_with_settings.lang,
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
    inactive_meeting = create_meetup(id=MEETING_ID_INACTIVE, active=False, owner=other_owner())
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(inactive_meeting)

    context, result = await call_handler(
        test_context.handler_id, handler_context=handler_context, with_meeting_id=test_context.meeting_id
    )

    assert result == ConversationHandler.END
    extra = (
        test_context.extra_metrics_non_owner_inactive
        if not isinstance(test_context.extra_metrics_non_owner_inactive, _Unset)
        else test_context.extra_metrics
    )
    metrics.assert_emitted(name=MetricKey.MEETING_NOT_OWNED, value=1)
    assert_handler_metrics(metrics, fault_value=0, extra_metrics=extra)
    assert_rejection_screen(
        context,
        update,
        factory.main_menu_view(RenderContext(lang=user_with_settings.lang)),
        lang=user_with_settings.lang,
    )
