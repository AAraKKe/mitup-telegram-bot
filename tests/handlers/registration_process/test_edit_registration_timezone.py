import logging
from unittest import mock

import pytest
from telegram import Location, Update
from telegram.ext import ConversationHandler

from mitup_bot.handlers.registration_process.edit_registration_timezone import (
    command_start_with_new_user,
    registration_timezone_invalid_input_handler,
    registration_timezone_location_message_handler,
    registration_timezone_text_message_handler,
)
from mitup_bot.handlers.registration_process.enums import ConversationRegistrationProcessState
from mitup_bot.models import User
from mitup_bot.models.users import UserStatus
from mitup_bot.utils import RegistrationMessages
from mitup_bot.views import factory
from tests.helpers import StubMitupContext, UpdateRequest, create_user
from tests.helpers.stub_db import MockDbSession


@pytest.fixture
def get_timezone_from_api():
    with mock.patch("mitup_bot.timezone_api.get_timezone_by_address") as timezone_patch:
        yield timezone_patch


@pytest.fixture
def get_location_from_api():
    with mock.patch("mitup_bot.timezone_api.get_timezone_by_location") as location_patch:
        yield location_patch


@pytest.mark.parametrize(
    "update",
    [
        UpdateRequest(command="start"),
        UpdateRequest(location=Location(latitude=1.0, longitude=1.0)),
    ],
    indirect=True,
    ids=["command", "location"],
)
async def test_registration_timezone_invalid_input_handler_sends_invalid_input_message_and_stays_in_timezone_state(
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    mock_session: MockDbSession,
):
    mock_session.add_object(user_with_settings, "tg_user_id")

    result = await registration_timezone_invalid_input_handler(update, context)

    context.api.assert_send_message_called(
        update,
        RegistrationMessages.TIMEZONE_INVALID_INPUT.get(lang=user_with_settings.lang),
    )
    assert result == ConversationRegistrationProcessState.TIMEZONE


async def test_registration_timezone_text_message_handler_sets_timezone_and_ends_conversation(
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    mock_session: MockDbSession,
    get_timezone_from_api: mock.MagicMock,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    assert update.effective_message is not None
    get_timezone_from_api.return_value = update.effective_message.text

    result = await registration_timezone_text_message_handler(update, context)

    view = factory.main_menu_view(lang=user_with_settings.lang).with_context(
        RegistrationMessages.TIMEZONE_SUCCESS.get(timezone=update.effective_message.text)
    )
    mock_session.assert_flushed()
    assert user_with_settings.settings.timezone == update.effective_message.text
    context.api.assert_send_message_called(update, view)
    assert result == ConversationHandler.END


async def test_registration_timezone_text_message_handler_stays_in_timezone_state_when_address_not_recognized(
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    mock_session: MockDbSession,
    get_timezone_from_api: mock.MagicMock,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    get_timezone_from_api.return_value = None

    result = await registration_timezone_text_message_handler(update, context)

    context.api.assert_send_message_called(
        update,
        RegistrationMessages.TIMEZONE_FAIL.get(lang=user_with_settings.lang),
    )
    assert result == ConversationRegistrationProcessState.TIMEZONE


@pytest.mark.parametrize("update", [UpdateRequest(location=Location(123.6, 103.5))], indirect=True)
async def test_registration_timezone_location_message_handler_sets_timezone_and_ends_conversation(
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    mock_session: MockDbSession,
    get_location_from_api: mock.MagicMock,
):
    mock_session.add_object(user_with_settings, "tg_user_id")
    get_location_from_api.return_value = "Europe/Madrid"

    result = await registration_timezone_location_message_handler(update, context)

    view = factory.main_menu_view(lang=user_with_settings.lang).with_context(
        RegistrationMessages.TIMEZONE_SUCCESS.get(timezone="Europe/Madrid")
    )
    mock_session.assert_flushed()
    assert user_with_settings.settings.timezone == "Europe/Madrid"
    context.api.assert_send_message_called(update, view)
    assert result == ConversationHandler.END


@pytest.mark.parametrize("update", [UpdateRequest(location=Location(123.6, 103.5))], indirect=True)
async def test_registration_timezone_location_message_handler_stays_in_timezone_state_when_location_not_recognized(
    update: Update,
    context: StubMitupContext,
    user_with_settings: User,
    mock_session: MockDbSession,
    get_location_from_api: mock.MagicMock,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.WARNING)
    mock_session.add_object(user_with_settings, "tg_user_id")
    get_location_from_api.return_value = None

    result = await registration_timezone_location_message_handler(update, context)

    context.api.assert_send_message_called(
        update,
        RegistrationMessages.TIMEZONE_FAIL.get(lang=user_with_settings.lang),
    )
    assert result == ConversationRegistrationProcessState.TIMEZONE

    # Issue #161: the warning must not leak the user's GPS coordinates.
    assert "123.6" not in caplog.text  # latitude
    assert "103.5" not in caplog.text  # longitude
    assert "tried to set a location" not in caplog.text  # old leaking phrase
    # structlog event string is the LogRecord message; user_id rides along as a record attribute.
    assert "User provided an invalid location, retrying" in caplog.text
    assert caplog.records[0].__dict__["user_id"] == user_with_settings.db_id


# ---------------------------------------------------------------------------
# JOINED_ONLY → MEMBER upgrade at end of timezone conversation
#
# Phase 3 designates the success path of the timezone conversation as the
# only legitimate JOINED_ONLY → MEMBER write site. Bailing partway
# (invalid input, conversation abandoned) must NOT upgrade the user.
# ---------------------------------------------------------------------------


async def test_joined_only_user_promoted_to_member_on_text_success(
    update: Update,
    context: StubMitupContext,
    mock_session: MockDbSession,
    get_timezone_from_api: mock.MagicMock,
    lang: str,
):
    """Successful text-flow timezone set must promote a JOINED_ONLY user to MEMBER."""
    joined_only_user = create_user(id=5, tg_user_id=123, status=UserStatus.JOINED_ONLY)
    joined_only_user.settings.language = lang
    mock_session.add_object(joined_only_user, "tg_user_id")
    assert update.effective_message is not None
    get_timezone_from_api.return_value = update.effective_message.text

    result = await registration_timezone_text_message_handler(update, context)

    assert joined_only_user.status is UserStatus.MEMBER
    assert result == ConversationHandler.END


async def test_joined_only_user_stays_joined_only_when_address_not_recognized(
    update: Update,
    context: StubMitupContext,
    mock_session: MockDbSession,
    get_timezone_from_api: mock.MagicMock,
    lang: str,
):
    """When the address is not recognised, status MUST remain JOINED_ONLY.

    The handler returns to the TIMEZONE state for retry, so a future success
    would still promote — but until then the user keeps their original status.
    """
    joined_only_user = create_user(id=5, tg_user_id=123, status=UserStatus.JOINED_ONLY)
    joined_only_user.settings.language = lang
    mock_session.add_object(joined_only_user, "tg_user_id")
    get_timezone_from_api.return_value = None

    result = await registration_timezone_text_message_handler(update, context)

    assert joined_only_user.status is UserStatus.JOINED_ONLY
    assert result == ConversationRegistrationProcessState.TIMEZONE


async def test_left_user_completes_conversation_and_is_promoted_to_member(
    update: Update,
    context: StubMitupContext,
    mock_session: MockDbSession,
    get_timezone_from_api: mock.MagicMock,
    lang: str,
):
    """A LEFT user re-onboarding through /start completes the conversation and becomes MEMBER.

    `get_or_create_onboarding_user` reuses the existing row for LEFT users (same as
    JOINED_ONLY), and the success-path write promotes them to MEMBER.
    """
    left_user = create_user(id=5, tg_user_id=123, status=UserStatus.LEFT)
    left_user.settings.language = lang
    mock_session.add_object(left_user, "tg_user_id")
    assert update.effective_message is not None
    get_timezone_from_api.return_value = update.effective_message.text

    result = await registration_timezone_text_message_handler(update, context)

    assert left_user.status is UserStatus.MEMBER
    assert result == ConversationHandler.END


@pytest.mark.parametrize("update", [UpdateRequest(location=Location(123.6, 103.5))], indirect=True)
async def test_joined_only_user_promoted_to_member_on_location_success(
    update: Update,
    context: StubMitupContext,
    mock_session: MockDbSession,
    get_location_from_api: mock.MagicMock,
    lang: str,
):
    """Successful location-flow timezone set also promotes a JOINED_ONLY user to MEMBER."""
    joined_only_user = create_user(id=5, tg_user_id=123, status=UserStatus.JOINED_ONLY)
    joined_only_user.settings.language = lang
    mock_session.add_object(joined_only_user, "tg_user_id")
    get_location_from_api.return_value = "Europe/Madrid"

    result = await registration_timezone_location_message_handler(update, context)

    assert joined_only_user.status is UserStatus.MEMBER
    assert result == ConversationHandler.END


@pytest.mark.parametrize("update", [UpdateRequest(location=Location(123.6, 103.5))], indirect=True)
async def test_joined_only_user_stays_joined_only_when_location_not_recognized(
    update: Update,
    context: StubMitupContext,
    mock_session: MockDbSession,
    get_location_from_api: mock.MagicMock,
    lang: str,
):
    joined_only_user = create_user(id=5, tg_user_id=123, status=UserStatus.JOINED_ONLY)
    joined_only_user.settings.language = lang
    mock_session.add_object(joined_only_user, "tg_user_id")
    get_location_from_api.return_value = None

    result = await registration_timezone_location_message_handler(update, context)

    assert joined_only_user.status is UserStatus.JOINED_ONLY
    assert result == ConversationRegistrationProcessState.TIMEZONE


# ---------------------------------------------------------------------------
# Brand-new user (no row) onboarding → ends up MEMBER
#
# `command_start_with_new_user` creates the row through
# `get_or_create_onboarding_user`. The default status from the model is
# MEMBER, and the success-path write re-confirms it.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("update", [UpdateRequest(command="start")], indirect=True)
async def test_brand_new_user_starts_with_member_status(
    update: Update,
    context: StubMitupContext,
    mock_session: MockDbSession,
):
    """A brand-new user (no row) hitting /start gets a freshly-created row with MEMBER status.

    No prior User row → `get_or_create_onboarding_user` constructs a new one. The
    `status` column defaults to MEMBER from the model — there is no JOINED_ONLY
    intermediate state here.
    """
    await command_start_with_new_user(update, context)

    # Exactly one User object was added to the session — and its status is MEMBER (the model default).
    added_users = [obj for obj in mock_session.objects_added if isinstance(obj, User)]
    assert len(added_users) == 1
    assert added_users[0].status is UserStatus.MEMBER


async def test_brand_new_user_completes_conversation_with_member_status(
    update: Update,
    context: StubMitupContext,
    mock_session: MockDbSession,
    get_timezone_from_api: mock.MagicMock,
    lang: str,
):
    """A brand-new row coming out of the conversation has status MEMBER.

    The write at the end of the success path is a no-op for brand-new rows (already
    MEMBER from the model default), but the assertion guards against accidental
    regressions (e.g. flipping the default to JOINED_ONLY without updating the
    success-path write).
    """
    # The user was just created — model default applies, but we go through the same flow.
    brand_new_user = create_user(id=99, tg_user_id=123, status=UserStatus.MEMBER)
    brand_new_user.settings.language = lang
    mock_session.add_object(brand_new_user, "tg_user_id")
    assert update.effective_message is not None
    get_timezone_from_api.return_value = update.effective_message.text

    result = await registration_timezone_text_message_handler(update, context)

    assert brand_new_user.status is UserStatus.MEMBER
    assert result == ConversationHandler.END
