"""No log line carries user-authored text.

Every assertion here is written the long way round — find the line, prove it exists, *then* prove
the field is absent — because the short way round passes for the wrong reason. "The address does
not appear in the logs" also holds when the line was renamed, when the handler returned early, and
when nothing was logged at all.
"""

import logging
from unittest import mock

import pytest
from telegram import Update

from mitup_bot.handlers.registration_process.edit_registration_timezone import (
    registration_timezone_text_message_handler,
    start_onboarding,
)
from mitup_bot.timezone_api import TimezoneLookupFailure
from tests.helpers import StubMitupContext, UpdateRequest, claimed_state, create_user, log_record
from tests.helpers.stub_db import MockDbSession

# The address a user types is the free-text input the timezone step exists to consume, so it is the
# sharpest case: the line has to say the lookup failed without saying what was typed.
TYPED_ADDRESS = "Calle de la Paz 14, Valencia"

# A deep-link token, in the shape `/start` delivers it. Telegram restricts the payload to base64url
# characters, which is exactly wide enough to smuggle arbitrary caller-chosen content onto a line.
START_PAYLOAD_TOKEN = "abc123deadbeef"


@pytest.fixture
def failing_timezone_lookup():
    with mock.patch("mitup_bot.timezone_api.get_timezone_by_address") as lookup:
        lookup.return_value = TimezoneLookupFailure.ADDRESS_NOT_GEOCODED
        yield lookup


@pytest.mark.parametrize("update", [UpdateRequest(message_text=TYPED_ADDRESS)], indirect=True)
async def test_a_failed_timezone_lookup_measures_the_address_instead_of_quoting_it(
    update: Update,
    context: StubMitupContext,
    mock_session: MockDbSession,
    failing_timezone_lookup: mock.MagicMock,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    mock_session.add_object(create_user(id=5, tg_user_id=123), "tg_user_id")

    await claimed_state(registration_timezone_text_message_handler(update, context))

    retried = log_record(caplog, "Onboarding step retried")
    fields = retried.__dict__

    # The line exists and is actionable: it names the branch and sizes the input.
    assert retried.levelname == "WARNING"
    assert fields["reason"] == TimezoneLookupFailure.ADDRESS_NOT_GEOCODED.value
    assert fields["address_length"] == len(TYPED_ADDRESS)

    # Only now is the absence meaningful.
    assert "address" not in fields
    assert not any(TYPED_ADDRESS in str(value) for value in fields.values())


@pytest.mark.parametrize(
    "update", [UpdateRequest(command="start", command_args=f"inline_{START_PAYLOAD_TOKEN}")], indirect=True
)
async def test_the_onboarding_lines_never_carry_the_raw_start_payload(
    update: Update,
    context: StubMitupContext,
    mock_session: MockDbSession,
    caplog: pytest.LogCaptureFixture,
):
    """A `/start` payload is client-supplied and unbounded, so whatever a line says about where a
    user arrived from comes from a closed vocabulary the payload was normalized into, never from
    the payload itself."""
    caplog.set_level(logging.INFO)

    await start_onboarding(update, context)

    # Prove the funnel ran and wrote the lines this asserts against, before asserting on absence.
    assert log_record(caplog, "Onboarding user created").__dict__["reason"] == "no_existing_row"
    onboarding_lines = [record for record in caplog.records if record.message.startswith("Onboarding")]
    assert len(onboarding_lines) >= 2, f"the funnel wrote {len(onboarding_lines)} lines"

    for record in onboarding_lines:
        assert "payload" not in record.__dict__
        assert not any(START_PAYLOAD_TOKEN in str(value) for value in record.__dict__.values())
