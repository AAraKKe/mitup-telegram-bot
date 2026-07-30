import logging
from unittest import mock

import pytest
from telegram import Update

from mitup_bot.acquisition import AcquisitionSource
from mitup_bot.handlers.registration_process.edit_registration_timezone import (
    registration_timezone_text_message_handler,
    start_onboarding,
)
from mitup_bot.models import User
from mitup_bot.models.users import UserStatus
from tests.helpers import StubMitupContext, UpdateRequest, claimed_state, create_user, log_record
from tests.helpers.stub_db import MockDbSession


@pytest.fixture
def get_timezone_from_api():
    with mock.patch("mitup_bot.timezone_api.get_timezone_by_address") as timezone_patch:
        yield timezone_patch


@pytest.mark.parametrize("update", [UpdateRequest(command="start")], indirect=True)
async def test_first_start_opens_the_funnel_for_a_brand_new_user(
    update: Update,
    context: StubMitupContext,
    mock_session: MockDbSession,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)

    await start_onboarding(update, context)

    created = log_record(caplog, "Onboarding user created")
    assert created.__dict__["reason"] == "no_existing_row"
    assert created.__dict__["status"] == UserStatus.MEMBER.value

    shown = log_record(caplog, "Onboarding step shown")
    assert shown.__dict__["step"] == "timezone_prompt"
    assert shown.__dict__["is_new_user"] is True


@pytest.mark.parametrize("update", [UpdateRequest(command="start")], indirect=True)
async def test_the_deep_link_a_new_user_followed_is_stamped_and_named_normalized(
    update: Update,
    context: StubMitupContext,
    mock_session: MockDbSession,
    caplog: pytest.LogCaptureFixture,
):
    """The payload reaches the row raw and the funnel line normalized — the line may not repeat text
    the sender chose."""
    caplog.set_level(logging.INFO)
    context.args = ["src_web"]

    await start_onboarding(update, context)

    created_users = [obj for obj in mock_session.objects_added if isinstance(obj, User)]
    assert [created.acquisition_source for created in created_users] == ["src_web"]
    assert log_record(caplog, "Onboarding user created").__dict__["source"] == AcquisitionSource.WEB.value


@pytest.mark.parametrize("update", [UpdateRequest(command="start")], indirect=True)
async def test_start_by_a_returning_left_user_reuses_the_row(
    update: Update,
    context: StubMitupContext,
    mock_session: MockDbSession,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    mock_session.add_object(create_user(id=5, tg_user_id=123, status=UserStatus.LEFT), "tg_user_id")

    await start_onboarding(update, context)

    resolved = log_record(caplog, "Onboarding user resolved")
    assert resolved.__dict__["reason"] == "existing_row"
    assert resolved.__dict__["status"] == UserStatus.LEFT.value
    assert log_record(caplog, "Onboarding step shown").__dict__["is_new_user"] is False


@pytest.mark.parametrize("update", [UpdateRequest(message_text="Madrid")], indirect=True)
async def test_completing_the_funnel_records_the_write_the_promotion_and_the_finish(
    update: Update,
    context: StubMitupContext,
    mock_session: MockDbSession,
    get_timezone_from_api: mock.MagicMock,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    returning_user = create_user(id=5, tg_user_id=123, status=UserStatus.LEFT)
    old_timezone = returning_user.settings.timezone
    mock_session.add_object(returning_user, "tg_user_id")
    get_timezone_from_api.return_value = "Europe/Madrid"

    await claimed_state(registration_timezone_text_message_handler(update, context))

    written = log_record(caplog, "User timezone set")
    assert written.__dict__["old_timezone"] == old_timezone
    assert written.__dict__["new_timezone"] == "Europe/Madrid"
    assert written.__dict__["source"] == "onboarding"
    assert written.__dict__["input_method"] == "message"

    promoted = log_record(caplog, "User promoted to member")
    assert promoted.__dict__["prior_status"] == UserStatus.LEFT.value
    assert promoted.__dict__["reason"] == "onboarding_completed"

    assert log_record(caplog, "Onboarding completed").__dict__["timezone"] == "Europe/Madrid"


@pytest.mark.parametrize("update", [UpdateRequest(message_text="Madrid")], indirect=True)
async def test_an_already_member_user_writes_no_promotion_line(
    update: Update,
    context: StubMitupContext,
    mock_session: MockDbSession,
    get_timezone_from_api: mock.MagicMock,
    caplog: pytest.LogCaptureFixture,
):
    """`set_status` is a no-op for a MEMBER, so a transition line there would be a lie."""
    caplog.set_level(logging.INFO)
    mock_session.add_object(create_user(id=5, tg_user_id=123, status=UserStatus.MEMBER), "tg_user_id")
    get_timezone_from_api.return_value = "Europe/Madrid"

    await claimed_state(registration_timezone_text_message_handler(update, context))

    assert "User promoted to member" not in {record.message for record in caplog.records}
    assert log_record(caplog, "Onboarding completed") is not None
