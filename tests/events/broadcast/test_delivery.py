import datetime as dt

import pytest
from structlog.testing import capture_logs
from telegram.error import BadRequest, NetworkError, RetryAfter

from mitup_bot.events.broadcast import delivery
from mitup_bot.events.broadcast.types import (
    MAX_DELIVERY_ATTEMPTS,
    RETRY_AFTER_MARGIN_SECONDS,
    RETRY_BACKOFF_BASE_SECONDS,
    DeliveryClassification,
    DeliveryOutcome,
    PendingDelivery,
)
from mitup_bot.exceptions import InactiveUserInteraction
from mitup_bot.models.broadcasts import BroadcastDeliveryStatus
from mitup_bot.views.factory import broadcast_recipient_view
from tests.helpers import MockApi, create_member

SENT = BroadcastDeliveryStatus.SENT
FAILED = BroadcastDeliveryStatus.FAILED
SKIPPED = BroadcastDeliveryStatus.SKIPPED_INACTIVE
RETRY_PENDING = BroadcastDeliveryStatus.RETRY_PENDING


@pytest.mark.parametrize(
    "side_effect, expected_status, expected_error, expected_flood",
    [
        (None, SENT, None, False),
        (InactiveUserInteraction(10, private=True), SKIPPED, "bot blocked by user", False),
        (BadRequest("bad payload"), FAILED, "bad payload", False),
        (RuntimeError("boom"), RETRY_PENDING, "boom", False),
        (RetryAfter(30), RETRY_PENDING, "Flood control exceeded. Retry in 30 seconds", True),
    ],
    ids=["sent", "inactive", "bad_request", "generic_retryable", "flood_control"],
)
async def test_deliver_one_classifies_outcome(
    api: MockApi,
    side_effect: object,
    expected_status: BroadcastDeliveryStatus,
    expected_error: str | None,
    expected_flood: bool,
):
    user = create_member(1, 10)
    view = broadcast_recipient_view("<b>hi</b>", "en")
    if side_effect is not None:
        api.mock_method("send_message_to_user").side_effect = side_effect

    classification = await delivery.deliver_one(api, user, view, attempt_count=1)

    assert classification.status is expected_status
    assert classification.error == expected_error
    assert classification.flood_control is expected_flood
    # The prebuilt view is sent through unchanged.
    api.assert_send_message_to_user_called(user=user, view=view)


async def test_deliver_one_logs_unexpected_error(api: MockApi):
    """The delivery row keeps only str(error); the traceback of an unexpected failure must land in
    a log line or a systemic bug is invisible behind a growing retry backlog."""
    user = create_member(1, 10)
    view = broadcast_recipient_view("<b>hi</b>", "en")
    error = RuntimeError("boom")
    api.mock_method("send_message_to_user").side_effect = error

    with capture_logs() as logs:
        await delivery.deliver_one(api, user, view, attempt_count=1)

    warnings = [entry for entry in logs if entry["event"] == "Unexpected broadcast delivery error"]
    assert len(warnings) == 1
    assert warnings[0]["log_level"] == "warning"
    assert warnings[0]["exc_info"] is error
    assert warnings[0]["tg_user_id"] == user.tg_user_id


async def test_deliver_one_flood_control_backoff_honors_retry_after_plus_margin(api: MockApi):
    api.mock_method("send_message_to_user").side_effect = RetryAfter(30)

    classification = await delivery.deliver_one(
        api, create_member(1, 10), broadcast_recipient_view("hi", "en"), attempt_count=1
    )

    assert classification.retry_delay == dt.timedelta(seconds=30 + RETRY_AFTER_MARGIN_SECONDS)


@pytest.mark.parametrize("attempt_count", [1, 2, 3], ids=["attempt_1", "attempt_2", "attempt_3"])
async def test_deliver_one_unknown_error_backoff_doubles_per_attempt(api: MockApi, attempt_count: int):
    api.mock_method("send_message_to_user").side_effect = RuntimeError("boom")

    classification = await delivery.deliver_one(
        api, create_member(1, 10), broadcast_recipient_view("hi", "en"), attempt_count=attempt_count
    )

    expected = dt.timedelta(seconds=RETRY_BACKOFF_BASE_SECONDS * 2 ** (attempt_count - 1))
    assert classification.retry_delay == expected


async def test_deliver_one_reraises_network_error(api: MockApi):
    user = create_member(1, 10)
    api.mock_method("send_message_to_user").side_effect = NetworkError("gateway down")

    with pytest.raises(NetworkError):
        await delivery.deliver_one(api, user, broadcast_recipient_view("hi", "en"), attempt_count=1)


async def test_resolve_delivery_outcome_schedules_retry_under_cap():
    pending = PendingDelivery(101, create_member(1, 10), "en", attempt_count=MAX_DELIVERY_ATTEMPTS - 1)
    classification = DeliveryClassification(RETRY_PENDING, "boom", retry_delay=dt.timedelta(seconds=60))

    before = dt.datetime.now(dt.UTC)
    outcome = delivery.resolve_delivery_outcome(pending, classification)
    after = dt.datetime.now(dt.UTC)

    assert outcome.status is RETRY_PENDING
    assert outcome.next_attempt_time is not None
    assert before + dt.timedelta(seconds=60) <= outcome.next_attempt_time <= after + dt.timedelta(seconds=60)


async def test_resolve_delivery_outcome_fails_permanently_at_cap():
    pending = PendingDelivery(101, create_member(1, 10), "en", attempt_count=MAX_DELIVERY_ATTEMPTS)
    classification = DeliveryClassification(RETRY_PENDING, "boom", retry_delay=dt.timedelta(seconds=60))

    outcome = delivery.resolve_delivery_outcome(pending, classification)

    assert outcome.status is FAILED
    assert outcome.next_attempt_time is None


def test_build_recipient_views_precomputes_one_view_per_language():
    views = delivery.build_recipient_views({"en": "<b>hi</b>", "es_ES": "hola"})

    # One prebuilt view per language, each identical to the shared factory view (so it equals the
    # operator preview) — built once here rather than per recipient.
    assert set(views) == {"en", "es_ES"}
    assert views["en"] == broadcast_recipient_view("<b>hi</b>", "en")
    assert views["es_ES"] == broadcast_recipient_view("hola", "es_ES")


async def test_deliver_batch_collects_outcomes_in_order(api: MockApi):
    first = create_member(1, 11, "en")
    second = create_member(2, 12, "es_ES")
    batch = [PendingDelivery(101, first, "en", 1), PendingDelivery(102, second, "es_ES", 1)]
    api.mock_method("send_message_to_user").side_effect = [None, BadRequest("nope")]

    result = await delivery.deliver_batch(api, 7, batch, delivery.build_recipient_views({"en": "hi", "es_ES": "hola"}))

    assert [(outcome.delivery_id, outcome.user_id, outcome.status) for outcome in result.outcomes] == [
        (101, 1, SENT),
        (102, 2, FAILED),
    ]
    assert result.flood_control is False
    assert api.mock_method("send_message_to_user").await_count == 2


async def test_deliver_batch_halts_mid_batch_on_flood_control_and_carries_out_remainder(api: MockApi):
    first = create_member(1, 11, "en")
    second = create_member(2, 12, "en")
    third = create_member(3, 13, "en")
    batch = [
        PendingDelivery(101, first, "en", 1),
        PendingDelivery(102, second, "en", 1),
        PendingDelivery(103, third, "en", 2),
    ]
    # First send succeeds, the second hits flood control; the third must never be attempted.
    api.mock_method("send_message_to_user").side_effect = [None, RetryAfter(20)]

    result = await delivery.deliver_batch(api, 7, batch, delivery.build_recipient_views({"en": "hi"}))

    assert result.flood_control is True
    # Only the first two rows were sent; the loop stopped at the flood hit.
    assert api.mock_method("send_message_to_user").await_count == 2
    assert [(outcome.delivery_id, outcome.status) for outcome in result.outcomes] == [(101, SENT), (102, RETRY_PENDING)]
    # The untried remainder is carried out for release, with the triggering row's backoff.
    assert [pending.delivery_id for pending in result.unattempted] == [103]
    assert result.flood_backoff == dt.timedelta(seconds=20 + RETRY_AFTER_MARGIN_SECONDS)


@pytest.mark.parametrize(
    "status, expected_level",
    [(SENT, "info"), (FAILED, "warning")],
    ids=["sent_info", "failed_warning"],
)
def test_log_delivery_level_matches_outcome(status: BroadcastDeliveryStatus, expected_level: str):
    user = create_member(1, 55, "en")
    outcome = DeliveryOutcome(1, user.db_id, status)
    classification = DeliveryClassification(status, "reason" if status is FAILED else None)

    with capture_logs() as logs:
        delivery.log_delivery(9, PendingDelivery(1, user, "en", 2), outcome, classification)

    entry = logs[0]
    assert entry["event"] == "broadcast_delivery"
    assert entry["log_level"] == expected_level
    assert entry["broadcast_id"] == 9
    assert entry["tg_user_id"] == 55
    assert entry["outcome"] == status.value
    assert entry["attempt"] == 2
    assert entry["retry_in"] is None


def test_log_delivery_records_retry_in_for_scheduled_retry():
    user = create_member(1, 55, "en")
    outcome = DeliveryOutcome(1, user.db_id, RETRY_PENDING, dt.datetime.now(dt.UTC))
    classification = DeliveryClassification(RETRY_PENDING, "boom", retry_delay=dt.timedelta(seconds=65))

    with capture_logs() as logs:
        delivery.log_delivery(9, PendingDelivery(1, user, "en", 1), outcome, classification)

    assert logs[0]["retry_in"] == 65
    assert logs[0]["outcome"] == RETRY_PENDING.value
