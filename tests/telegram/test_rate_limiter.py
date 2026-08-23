import logging
from collections.abc import Callable, Coroutine
from time import perf_counter
from typing import Any
from unittest import mock

import pytest
from aiolimiter import AsyncLimiter
from telegram.error import RetryAfter

from mitup_bot.monitoring import MetricsClient, MetricUnit, bound_metrics_client
from mitup_bot.rate_limiter import (
    MitupRateLimiter,
    RequestScope,
    RetryAfterPauses,
    request_key,
)
from tests.helpers.logs import log_record
from tests.helpers.monitoring import MetricAssertions, make_test_metrics_client

PRIVATE_CHAT_ID = 4242
OTHER_PRIVATE_CHAT_ID = 5353
GROUP_CHAT_ID = -100777
INLINE_MESSAGE_ID = "BAAAAF9tZXNzYWdl"
# Spelled literally rather than imported, so renaming the event in the module fails here instead
# of travelling silently into the assertion.
THROTTLED_EVENT = "Telegram rate limit hit"
THROTTLE_METRIC = "TelegramThrottled"


@pytest.fixture
def client() -> MetricsClient:
    return make_test_metrics_client()


@pytest.fixture
def metrics(client: MetricsClient) -> MetricAssertions:
    return MetricAssertions(client)


def succeeding_callback() -> mock.AsyncMock:
    return mock.AsyncMock(return_value={"ok": True})


def overall_bucket(limiter: MitupRateLimiter) -> AsyncLimiter:
    """The bot-wide bucket, which the base class builds for any positive overall rate."""
    assert limiter._base_limiter is not None
    return limiter._base_limiter


async def run_request(
    limiter: MitupRateLimiter,
    callback: Callable[..., Coroutine[Any, Any, Any]],
    *,
    endpoint: str = "sendMessage",
    rate_limit_args: int | None = None,
    **data: Any,
) -> Any:
    return await limiter.process_request(
        callback=callback,
        args=(),
        kwargs={},
        endpoint=endpoint,
        data=data,
        rate_limit_args=rate_limit_args,
    )


# --- Key derivation ---


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"chat_id": PRIVATE_CHAT_ID}, RequestScope.CHAT),
        ({"chat_id": str(PRIVATE_CHAT_ID)}, RequestScope.CHAT),
        ({"chat_id": GROUP_CHAT_ID}, RequestScope.GROUP),
        ({"chat_id": str(GROUP_CHAT_ID)}, RequestScope.GROUP),
        ({"chat_id": "@a_channel"}, RequestScope.GROUP),
        ({"inline_message_id": INLINE_MESSAGE_ID}, RequestScope.INLINE_MESSAGE),
        ({}, RequestScope.UNKEYED),
    ],
    ids=["private", "private_as_string", "group", "group_as_string", "username", "inline", "unkeyed"],
)
def test_a_request_is_keyed_by_the_flood_limit_it_is_addressed_under(data: dict[str, Any], expected: RequestScope):
    assert request_key(data).scope is expected


def test_only_a_request_addressing_nobody_is_left_out_of_the_overall_bucket():
    assert request_key({"chat_id": PRIVATE_CHAT_ID}).counts_against_overall
    assert request_key({"chat_id": GROUP_CHAT_ID}).counts_against_overall
    assert request_key({"inline_message_id": INLINE_MESSAGE_ID}).counts_against_overall
    assert not request_key({}).counts_against_overall


def test_only_a_group_addressed_request_carries_the_per_group_bucket_key():
    assert request_key({"chat_id": GROUP_CHAT_ID}).group == GROUP_CHAT_ID
    assert request_key({"chat_id": PRIVATE_CHAT_ID}).group is False
    assert request_key({"inline_message_id": INLINE_MESSAGE_ID}).group is False


def test_every_inline_edit_shares_one_pause_key():
    """The chat behind an inline message is invisible, so two edits cannot be told apart by
    recipient; keying them per message id would make each one learn its own throttle."""
    first = request_key({"inline_message_id": INLINE_MESSAGE_ID})
    second = request_key({"inline_message_id": "BAAAAG90aGVy"})

    assert first.pause_key == second.pause_key


# --- Shaping ---


async def test_a_burst_of_inline_edits_is_held_back_once_the_overall_bucket_empties():
    """The gap this class exists to close: PTB gates the overall bucket on `chat_id`, so inline
    edits pass through it unshaped and uncounted. They are most of this bot's outbound volume."""
    limiter = MitupRateLimiter(overall_max_rate=2, overall_time_period=0.2)
    callback = succeeding_callback()

    started = perf_counter()
    for _ in range(4):
        await run_request(limiter, callback, inline_message_id=INLINE_MESSAGE_ID)
    elapsed = perf_counter() - started

    assert callback.await_count == 4
    # Two of the four had to wait for capacity to regenerate at 2 per 0.2s.
    assert elapsed >= 0.15


async def test_an_inline_edit_spends_overall_capacity_like_a_chat_addressed_request():
    limiter = MitupRateLimiter(overall_max_rate=1, overall_time_period=60)

    await run_request(limiter, succeeding_callback(), inline_message_id=INLINE_MESSAGE_ID)

    assert not overall_bucket(limiter).has_capacity()


async def test_a_chat_addressed_request_still_spends_overall_capacity():
    limiter = MitupRateLimiter(overall_max_rate=1, overall_time_period=60)

    await run_request(limiter, succeeding_callback(), chat_id=PRIVATE_CHAT_ID)

    assert not overall_bucket(limiter).has_capacity()


async def test_a_request_addressing_nobody_is_left_unshaped():
    """`getMe` and friends reach no recipient, so nothing about them counts toward a send limit.
    PTB leaves them out of the overall bucket and this class does not change that."""
    limiter = MitupRateLimiter(overall_max_rate=1, overall_time_period=60)

    await run_request(limiter, succeeding_callback(), endpoint="getMe")

    assert overall_bucket(limiter).has_capacity()


async def test_a_group_addressed_request_spends_its_own_group_bucket():
    limiter = MitupRateLimiter(group_max_rate=1, group_time_period=60)

    await run_request(limiter, succeeding_callback(), chat_id=GROUP_CHAT_ID)

    assert not limiter._get_group_limiter(GROUP_CHAT_ID).has_capacity()


# --- Throttle observability ---


async def test_a_throttle_is_reported_on_both_planes_before_anything_sleeps(
    caplog: pytest.LogCaptureFixture, client: MetricsClient, metrics: MetricAssertions
):
    limiter = MitupRateLimiter()
    callback = mock.AsyncMock(side_effect=RetryAfter(7))

    with caplog.at_level(logging.WARNING), bound_metrics_client(client):
        with pytest.raises(RetryAfter):
            await run_request(limiter, callback, endpoint="editMessageText", chat_id=PRIVATE_CHAT_ID)

    record = log_record(caplog, THROTTLED_EVENT)
    assert record.levelno == logging.WARNING
    assert record.__dict__["api_method"] == "editMessageText"
    assert record.__dict__["scope"] == "chat"
    assert record.__dict__["retry_after_s"] == 7
    assert record.__dict__["outcome"] == "exhausted"
    metrics.assert_emitted(name=THROTTLE_METRIC, value=1, unit=MetricUnit.COUNT, times=1)


async def test_a_throttled_request_records_the_method_and_never_an_id(
    caplog: pytest.LogCaptureFixture,
):
    """`api_method` is the endpoint PTB names and never a URL, because every Bot API URL embeds the
    token. The recipient rides only as the bounded `scope`: an inline message id is not a registry
    field and a chat id here is not the update's, so binding one would shadow the ambient value."""
    limiter = MitupRateLimiter()

    with caplog.at_level(logging.WARNING):
        with pytest.raises(RetryAfter):
            await run_request(
                limiter,
                mock.AsyncMock(side_effect=RetryAfter(3)),
                endpoint="editMessageReplyMarkup",
                inline_message_id=INLINE_MESSAGE_ID,
            )

    record = log_record(caplog, THROTTLED_EVENT)
    assert record.__dict__["api_method"] == "editMessageReplyMarkup"
    assert record.__dict__["scope"] == "inline_message"
    assert "inline_message_id" not in record.__dict__
    assert "chat_id" not in record.__dict__


async def test_a_group_throttle_names_the_group_scope(caplog: pytest.LogCaptureFixture):
    limiter = MitupRateLimiter()

    with caplog.at_level(logging.WARNING):
        with pytest.raises(RetryAfter):
            await run_request(limiter, mock.AsyncMock(side_effect=RetryAfter(1)), chat_id=GROUP_CHAT_ID)

    assert log_record(caplog, THROTTLED_EVENT).__dict__["scope"] == "group"


async def test_a_request_that_flowed_straight_through_reports_the_zero(
    client: MetricsClient, metrics: MetricAssertions
):
    """Without this sample the series exists only on the bad days, so no alarm can read a rate
    from it: a throttle resolves by waiting and the round trip still succeeds."""
    limiter = MitupRateLimiter()

    with bound_metrics_client(client):
        await run_request(limiter, succeeding_callback(), chat_id=PRIVATE_CHAT_ID)

    metrics.assert_emitted(name=THROTTLE_METRIC, value=0, unit=MetricUnit.COUNT, times=1)


async def test_a_request_held_back_by_a_depleted_bucket_counts_as_throttled(
    client: MetricsClient, metrics: MetricAssertions
):
    """Waiting for capacity is throttling too. A bot shaped hard enough never to draw a 429 is
    still being slowed down, and a series counting only 429s would call that healthy."""
    limiter = MitupRateLimiter(overall_max_rate=1, overall_time_period=0.2)

    with bound_metrics_client(client):
        await run_request(limiter, succeeding_callback(), chat_id=PRIVATE_CHAT_ID)
        await run_request(limiter, succeeding_callback(), chat_id=PRIVATE_CHAT_ID)

    metrics.assert_emitted(name=THROTTLE_METRIC, value=0, times=1)
    metrics.assert_emitted(name=THROTTLE_METRIC, value=1, times=1)


async def test_a_group_request_held_back_by_its_own_bucket_counts_as_throttled(
    client: MetricsClient, metrics: MetricAssertions
):
    limiter = MitupRateLimiter(group_max_rate=1, group_time_period=0.2)

    with bound_metrics_client(client):
        await run_request(limiter, succeeding_callback(), chat_id=GROUP_CHAT_ID)
        await run_request(limiter, succeeding_callback(), chat_id=GROUP_CHAT_ID)

    metrics.assert_emitted(name=THROTTLE_METRIC, value=1, times=1)


async def test_a_paid_broadcast_is_measured_against_the_bucket_it_actually_passes_through(
    client: MetricsClient, metrics: MetricAssertions
):
    """A paid broadcast bypasses the overall bucket for its own far higher one, so an empty
    overall bucket says nothing about whether it was held up."""
    limiter = MitupRateLimiter(overall_max_rate=1, overall_time_period=60)
    await run_request(limiter, succeeding_callback(), chat_id=PRIVATE_CHAT_ID)

    with bound_metrics_client(client):
        await run_request(limiter, succeeding_callback(), chat_id=OTHER_PRIVATE_CHAT_ID, allow_paid_broadcast=True)

    metrics.assert_emitted(name=THROTTLE_METRIC, value=0, times=1)


async def test_a_request_that_waited_out_a_held_pause_counts_as_throttled(
    client: MetricsClient, metrics: MetricAssertions
):
    limiter = MitupRateLimiter()
    limiter.pauses.hold(request_key({"chat_id": PRIVATE_CHAT_ID}).pause_key, 0.05)

    with bound_metrics_client(client):
        await run_request(limiter, succeeding_callback(), chat_id=PRIVATE_CHAT_ID)

    metrics.assert_emitted(name=THROTTLE_METRIC, value=1, times=1)


async def test_shaping_that_only_made_a_request_wait_logs_nothing(
    caplog: pytest.LogCaptureFixture, client: MetricsClient, metrics: MetricAssertions
):
    """The line marks the drastic case. With the buckets doing their job a 429 should be rare, so
    a line always means Telegram refused the call rather than that the limiter queued it."""
    limiter = MitupRateLimiter(overall_max_rate=1, overall_time_period=0.2)

    with caplog.at_level(logging.WARNING), bound_metrics_client(client):
        await run_request(limiter, succeeding_callback(), chat_id=PRIVATE_CHAT_ID)
        await run_request(limiter, succeeding_callback(), chat_id=PRIVATE_CHAT_ID)

    metrics.assert_emitted(name=THROTTLE_METRIC, value=1, times=1)
    assert not [record for record in caplog.records if record.message == THROTTLED_EVENT]


async def test_a_recovered_throttle_reports_one_sample_and_the_line(
    caplog: pytest.LogCaptureFixture, client: MetricsClient, metrics: MetricAssertions
):
    limiter = MitupRateLimiter(max_retries=1)
    callback = mock.AsyncMock(side_effect=[RetryAfter(0), {"ok": True}])

    with caplog.at_level(logging.WARNING), bound_metrics_client(client):
        response = await run_request(limiter, callback, chat_id=PRIVATE_CHAT_ID)

    assert response == {"ok": True}
    assert log_record(caplog, THROTTLED_EVENT).__dict__["outcome"] == "retrying"
    metrics.assert_emitted(name=THROTTLE_METRIC, value=1, times=1)
    metrics.assert_not_emitted(name=THROTTLE_METRIC, value=0)


async def test_a_request_reports_one_sample_however_many_attempts_it_took(
    client: MetricsClient, metrics: MetricAssertions
):
    """The series counts requests, not attempts, so its average stays readable as the share of
    traffic being throttled instead of drifting with the retry budget."""
    limiter = MitupRateLimiter(max_retries=2)

    with bound_metrics_client(client):
        with pytest.raises(RetryAfter):
            await run_request(limiter, mock.AsyncMock(side_effect=RetryAfter(0)), chat_id=PRIVATE_CHAT_ID)

    metrics.assert_emitted(name=THROTTLE_METRIC, times=1)


async def test_a_throttle_outside_an_invocation_still_reaches_the_log_plane(
    caplog: pytest.LogCaptureFixture,
):
    """There is no ambient client during process startup, and the limiter owns none that anything
    would flush, so the sample is dropped rather than emitted into a client that never drains."""
    limiter = MitupRateLimiter()

    with caplog.at_level(logging.WARNING):
        with pytest.raises(RetryAfter):
            await run_request(limiter, mock.AsyncMock(side_effect=RetryAfter(2)), chat_id=PRIVATE_CHAT_ID)

    assert log_record(caplog, THROTTLED_EVENT).__dict__["retry_after_s"] == 2


async def test_the_retry_budget_is_spent_before_the_throttle_is_re_raised():
    limiter = MitupRateLimiter(max_retries=2)
    callback = mock.AsyncMock(side_effect=RetryAfter(0))

    with pytest.raises(RetryAfter):
        await run_request(limiter, callback, chat_id=PRIVATE_CHAT_ID)

    assert callback.await_count == 3


async def test_a_caller_supplied_retry_budget_overrides_the_configured_one():
    limiter = MitupRateLimiter(max_retries=0)
    callback = mock.AsyncMock(side_effect=RetryAfter(0))

    with pytest.raises(RetryAfter):
        await run_request(limiter, callback, rate_limit_args=1, chat_id=PRIVATE_CHAT_ID)

    assert callback.await_count == 2


# --- Pause attribution ---


async def test_a_throttled_chat_does_not_pause_a_different_chat():
    """The production failure this fixes: PTB clears one process-wide event, so a single throttled
    message stops every unrelated chat for the whole `retry_after`."""
    limiter = MitupRateLimiter()

    with pytest.raises(RetryAfter):
        await run_request(limiter, mock.AsyncMock(side_effect=RetryAfter(30)), chat_id=PRIVATE_CHAT_ID)

    throttled = request_key({"chat_id": PRIVATE_CHAT_ID}).pause_key
    untouched = request_key({"chat_id": OTHER_PRIVATE_CHAT_ID}).pause_key
    assert limiter.pauses.remaining(throttled) > 0
    assert limiter.pauses.remaining(untouched) <= 0


async def test_a_pause_outlives_the_request_that_spent_its_last_retry():
    """`max_retries` defaults to 0, so a limiter that only paused on the way to a retry would
    never record a pause at all. The wait Telegram asked for binds the key, not the call."""
    limiter = MitupRateLimiter(max_retries=0)

    with pytest.raises(RetryAfter):
        await run_request(limiter, mock.AsyncMock(side_effect=RetryAfter(30)), chat_id=PRIVATE_CHAT_ID)

    assert limiter.pauses.remaining(request_key({"chat_id": PRIVATE_CHAT_ID}).pause_key) > 0


async def test_an_unrelated_chat_keeps_flowing_while_another_waits_out_its_pause():
    limiter = MitupRateLimiter()
    with pytest.raises(RetryAfter):
        await run_request(limiter, mock.AsyncMock(side_effect=RetryAfter(30)), chat_id=PRIVATE_CHAT_ID)

    callback = succeeding_callback()
    started = perf_counter()
    await run_request(limiter, callback, chat_id=OTHER_PRIVATE_CHAT_ID)

    assert perf_counter() - started < 1
    callback.assert_awaited_once()


async def test_a_throttle_nobody_can_be_blamed_for_pauses_every_key():
    """A 429 carries no scope, so a request addressing no recipient leaves nothing to attribute
    the pause to. The conservative answer is the only one left."""
    limiter = MitupRateLimiter()

    with pytest.raises(RetryAfter):
        await run_request(limiter, mock.AsyncMock(side_effect=RetryAfter(30)), endpoint="getMe")

    assert limiter.pauses.remaining(request_key({"chat_id": PRIVATE_CHAT_ID}).pause_key) > 0
    assert limiter.pauses.remaining(None) > 0


async def test_a_throttled_inline_edit_pauses_inline_edits_and_not_chat_traffic():
    limiter = MitupRateLimiter()

    with pytest.raises(RetryAfter):
        await run_request(limiter, mock.AsyncMock(side_effect=RetryAfter(30)), inline_message_id=INLINE_MESSAGE_ID)

    inline = request_key({"inline_message_id": "BAAAAG90aGVy"}).pause_key
    assert limiter.pauses.remaining(inline) > 0
    assert limiter.pauses.remaining(request_key({"chat_id": PRIVATE_CHAT_ID}).pause_key) <= 0


# --- The pause registry ---


def test_a_pause_covers_the_retry_after_it_was_given():
    pauses = RetryAfterPauses()
    key = (RequestScope.CHAT, PRIVATE_CHAT_ID)

    pauses.hold(key, 30)

    assert 29 < pauses.remaining(key) <= 30


def test_a_longer_pause_never_shortens_a_standing_one():
    pauses = RetryAfterPauses()
    key = (RequestScope.CHAT, PRIVATE_CHAT_ID)

    pauses.hold(key, 30)
    pauses.hold(key, 1)

    assert pauses.remaining(key) > 1


def test_a_global_pause_holds_back_a_key_with_no_pause_of_its_own():
    pauses = RetryAfterPauses()

    pauses.hold(None, 30)

    assert pauses.remaining((RequestScope.CHAT, PRIVATE_CHAT_ID)) > 0


def test_elapsed_pauses_are_dropped_so_the_map_holds_only_live_throttles():
    pauses = RetryAfterPauses()
    pauses.hold((RequestScope.CHAT, PRIVATE_CHAT_ID), 0)

    pauses.hold((RequestScope.CHAT, OTHER_PRIVATE_CHAT_ID), 30)

    assert list(pauses.deadlines) == [(RequestScope.CHAT, OTHER_PRIVATE_CHAT_ID)]


async def test_waiting_on_a_clear_key_returns_at_once():
    pauses = RetryAfterPauses()

    await pauses.wait((RequestScope.CHAT, PRIVATE_CHAT_ID))
