import datetime as dt
from collections.abc import Iterator, Sequence
from typing import Any, cast
from unittest import mock

import pytest
from sqlalchemy import Row
from structlog.testing import capture_logs
from telegram.error import BadRequest, NetworkError, RetryAfter

from mitup_bot.cli import send_broadcasts
from mitup_bot.cli.commands.recurrent_events import EventType
from mitup_bot.cli.send_broadcasts import (
    MAX_BROADCAST_ATTEMPTS,
    MAX_DELIVERY_ATTEMPTS,
    RETRY_AFTER_MARGIN_SECONDS,
    RETRY_BACKOFF_BASE_SECONDS,
    BatchResult,
    BroadcastSummary,
    ClaimedBroadcast,
    DeliveryClassification,
    DeliveryOutcome,
    LanguageBreakdown,
    PendingDelivery,
)
from mitup_bot.exceptions import InactiveUserInteraction
from mitup_bot.models import BroadcastMessage
from mitup_bot.models.broadcasts import BroadcastDeliveryStatus, BroadcastStatus
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient
from mitup_bot.utils.entities import FormattedText, parse_format_tags
from mitup_bot.utils.messages import BroadcastOperatorMessages
from tests.helpers import MockApi, MockDbSession, Result, create_broadcast, create_member
from tests.helpers.monitoring import MetricAssertions, make_test_metrics_client

SENT = BroadcastDeliveryStatus.SENT
FAILED = BroadcastDeliveryStatus.FAILED
SKIPPED = BroadcastDeliveryStatus.SKIPPED_INACTIVE
IN_PROGRESS = BroadcastDeliveryStatus.IN_PROGRESS
RETRY_PENDING = BroadcastDeliveryStatus.RETRY_PENDING


@pytest.fixture(autouse=True)
def capture_structlog() -> Iterator[None]:
    """Keep the job's structlog emissions off the real pipeline, mirroring the rest of the CLI
    suite: a bare emission trips the xdist + json-report reporter under coverage."""
    with capture_logs():
        yield


@pytest.fixture
def metrics_client() -> MetricsClient:
    return make_test_metrics_client(base_dimensions={"EventType": EventType.SEND_BROADCASTS.value})


@pytest.fixture
def metrics(metrics_client: MetricsClient) -> MetricAssertions:
    return MetricAssertions(metrics_client)


def make_summary(
    *,
    status: BroadcastStatus,
    name: str = "Campaign",
    attempts: int = 1,
    total: int = 4,
    sent: int = 3,
    failed: int = 1,
    skipped: int = 0,
    orphaned: int = 0,
    breakdown: list[LanguageBreakdown] | None = None,
) -> BroadcastSummary:
    return BroadcastSummary(
        name=name,
        status=status,
        attempts=attempts,
        total=total,
        sent=sent,
        failed=failed,
        skipped=skipped,
        orphaned=orphaned,
        breakdown=breakdown if breakdown is not None else [],
    )


def script_exec(mock_session: MockDbSession, *results: Result):
    """Replace the mock session's `exec` with a scripted queue of results consumed in call order.

    The sender's inserts, UPDATE...RETURNING claims, aggregates and a repeated `count(*)` (once
    before and once after materialization) can't all be keyed by SQL string — the two counts share
    a statement yet must return different values, and the update carries a live timestamp literal —
    so tests drive `exec` positionally instead of through the statement registry."""
    mock_session.exec = mock.AsyncMock(side_effect=list(results))


def batch_of(*outcomes: DeliveryOutcome) -> BatchResult:
    """Wrap resolved outcomes as a non-flood BatchResult — the shape record_batch_outcomes takes."""
    return BatchResult(outcomes=list(outcomes), flood_control=False)


# ---------------------------------------------------------------------------
# deliver_one — the five per-recipient outcome branches
# ---------------------------------------------------------------------------


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
    if side_effect is not None:
        api.mock_method("send_message_to_user").side_effect = side_effect

    classification = await send_broadcasts.deliver_one(api, user, "<b>hi</b>", attempt_count=1)

    assert classification.status is expected_status
    assert classification.error == expected_error
    assert classification.flood_control is expected_flood
    api.assert_send_message_to_user_called(user=user, view=parse_format_tags("<b>hi</b>", {}))


async def test_deliver_one_flood_control_backoff_honors_retry_after_plus_margin(api: MockApi):
    api.mock_method("send_message_to_user").side_effect = RetryAfter(30)

    classification = await send_broadcasts.deliver_one(api, create_member(1, 10), "hi", attempt_count=1)

    assert classification.retry_delay == dt.timedelta(seconds=30 + RETRY_AFTER_MARGIN_SECONDS)


@pytest.mark.parametrize("attempt_count", [1, 2, 3], ids=["attempt_1", "attempt_2", "attempt_3"])
async def test_deliver_one_unknown_error_backoff_doubles_per_attempt(api: MockApi, attempt_count: int):
    api.mock_method("send_message_to_user").side_effect = RuntimeError("boom")

    classification = await send_broadcasts.deliver_one(api, create_member(1, 10), "hi", attempt_count=attempt_count)

    expected = dt.timedelta(seconds=RETRY_BACKOFF_BASE_SECONDS * 2 ** (attempt_count - 1))
    assert classification.retry_delay == expected


async def test_deliver_one_reraises_network_error(api: MockApi):
    user = create_member(1, 10)
    api.mock_method("send_message_to_user").side_effect = NetworkError("gateway down")

    with pytest.raises(NetworkError):
        await send_broadcasts.deliver_one(api, user, "hi", attempt_count=1)


# ---------------------------------------------------------------------------
# resolve_delivery_outcome — the per-delivery attempt cap
# ---------------------------------------------------------------------------


async def test_resolve_delivery_outcome_schedules_retry_under_cap():
    pending = PendingDelivery(101, create_member(1, 10), "en", attempt_count=MAX_DELIVERY_ATTEMPTS - 1)
    classification = DeliveryClassification(RETRY_PENDING, "boom", retry_delay=dt.timedelta(seconds=60))

    before = dt.datetime.now(dt.UTC)
    outcome = send_broadcasts.resolve_delivery_outcome(pending, classification)
    after = dt.datetime.now(dt.UTC)

    assert outcome.status is RETRY_PENDING
    assert outcome.next_attempt_time is not None
    assert before + dt.timedelta(seconds=60) <= outcome.next_attempt_time <= after + dt.timedelta(seconds=60)


async def test_resolve_delivery_outcome_fails_permanently_at_cap():
    pending = PendingDelivery(101, create_member(1, 10), "en", attempt_count=MAX_DELIVERY_ATTEMPTS)
    classification = DeliveryClassification(RETRY_PENDING, "boom", retry_delay=dt.timedelta(seconds=60))

    outcome = send_broadcasts.resolve_delivery_outcome(pending, classification)

    assert outcome.status is FAILED
    assert outcome.next_attempt_time is None


# ---------------------------------------------------------------------------
# deliver_batch / log_delivery
# ---------------------------------------------------------------------------


async def test_deliver_batch_collects_outcomes_in_order(api: MockApi):
    first = create_member(1, 11, "en")
    second = create_member(2, 12, "es_ES")
    batch = [PendingDelivery(101, first, "en", 1), PendingDelivery(102, second, "es_ES", 1)]
    api.mock_method("send_message_to_user").side_effect = [None, BadRequest("nope")]

    result = await send_broadcasts.deliver_batch(api, 7, batch, {"en": "hi", "es_ES": "hola"})

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

    result = await send_broadcasts.deliver_batch(api, 7, batch, {"en": "hi"})

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
        send_broadcasts.log_delivery(9, PendingDelivery(1, user, "en", 2), outcome, classification)

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
        send_broadcasts.log_delivery(9, PendingDelivery(1, user, "en", 1), outcome, classification)

    assert logs[0]["retry_in"] == 65
    assert logs[0]["outcome"] == RETRY_PENDING.value


# ---------------------------------------------------------------------------
# Leaf DB helpers (explicit-session)
# ---------------------------------------------------------------------------


async def test_count_deliveries_reads_scalar(mock_session: MockDbSession):
    script_exec(mock_session, Result(results=(7,)))

    assert await send_broadcasts.count_deliveries(mock_session, 5) == 7


async def test_aggregate_delivery_counts_builds_language_status_map(mock_session: MockDbSession):
    script_exec(mock_session, Result(results=(("en", SENT, 3), ("es_ES", FAILED, 1))))

    counts = await send_broadcasts.aggregate_delivery_counts(mock_session, 5)

    assert counts == {("en", SENT): 3, ("es_ES", FAILED): 1}


@pytest.mark.parametrize("with_sent_time", [True, False], ids=["with_sent_time", "without_sent_time"])
async def test_mark_deliveries_issues_update(mock_session: MockDbSession, with_sent_time: bool):
    script_exec(mock_session, Result())
    sent_time = dt.datetime.now(dt.UTC) if with_sent_time else None

    await send_broadcasts.mark_deliveries(mock_session, [DeliveryOutcome(1, 10, SENT)], SENT, sent_time=sent_time)

    assert mock_session.exec.await_count == 1
    assert "UPDATE broadcast_deliveries" in mock_session.queries_executed[0]


async def test_resolve_claimed_recipients_pairs_users_to_deliveries(mock_session: MockDbSession):
    first = create_member(1, 11, "en")
    second = create_member(2, 12, "es_ES")
    script_exec(mock_session, Result(results=(first, second)))

    claimed = cast(Sequence[Row[Any]], [(101, 1, "en", 1), (102, 2, "es_ES", 3)])
    pending = await send_broadcasts.resolve_claimed_recipients(mock_session, claimed)

    assert [(item.delivery_id, item.user.db_id, item.language_sent, item.attempt_count) for item in pending] == [
        (101, 1, "en", 1),
        (102, 2, "es_ES", 3),
    ]


async def test_resolve_claimed_recipients_orders_batch_by_delivery_id_even_when_shuffled(
    mock_session: MockDbSession,
):
    """Postgres RETURNING does not preserve the claim subquery's ORDER BY, so the batch must be
    re-sorted by delivery id — this is load-bearing for the flood-halt semantics (lowest id is the
    flood trigger; the released remainder is the highest ids)."""
    first = create_member(1, 11, "en")
    second = create_member(2, 12, "en")
    third = create_member(3, 13, "en")
    script_exec(mock_session, Result(results=(first, second, third)))

    # RETURNING hands them back out of id order, as real Postgres may.
    claimed = cast(Sequence[Row[Any]], [(103, 3, "en", 1), (101, 1, "en", 2), (102, 2, "en", 3)])
    pending = await send_broadcasts.resolve_claimed_recipients(mock_session, claimed)

    assert [item.delivery_id for item in pending] == [101, 102, 103]
    assert [item.user.db_id for item in pending] == [1, 2, 3]
    assert [item.attempt_count for item in pending] == [2, 3, 1]


async def test_deactivate_skipped_users_marks_members_left(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions
):
    member = create_member(1, 20, status=UserStatus.MEMBER)
    script_exec(mock_session, Result(results=(member,)))

    await send_broadcasts.deactivate_skipped_users(mock_session, [DeliveryOutcome(1, 1, SKIPPED)], metrics_client)
    await metrics_client.flush()

    assert member.status is UserStatus.LEFT
    metrics.assert_emitted(name=MetricKey.INACTIVE_USER_SET, value=1)


async def test_deactivate_skipped_users_no_metric_when_nothing_transitions(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions
):
    already_left = create_member(1, 20, status=UserStatus.LEFT)
    script_exec(mock_session, Result(results=(already_left,)))

    await send_broadcasts.deactivate_skipped_users(mock_session, [DeliveryOutcome(1, 1, SKIPPED)], metrics_client)
    await metrics_client.flush()

    metrics.assert_not_emitted(name=MetricKey.INACTIVE_USER_SET)


@pytest.mark.parametrize("rowcount, expected", [(1, True), (0, False)], ids=["won", "lost"])
async def test_transition_to_terminal_reports_winner(mock_session: MockDbSession, rowcount: int, expected: bool):
    script_exec(mock_session, Result(rowcount=rowcount))

    assert await send_broadcasts.transition_to_terminal(mock_session, 5, BroadcastStatus.DONE) is expected


async def test_build_language_breakdown_maps_counts_onto_messages():
    broadcast = create_broadcast(id=5)
    broadcast.messages = [
        BroadcastMessage(language="en", body_html="hi"),
        BroadcastMessage(language="es_ES", body_html="hola"),
    ]
    counts = {("en", SENT): 3, ("en", FAILED): 1, ("es_ES", SKIPPED): 2, ("en", IN_PROGRESS): 4}

    breakdown = send_broadcasts.build_language_breakdown(broadcast, counts)

    assert [(line.language, line.sent, line.failed, line.skipped, line.orphaned) for line in breakdown] == [
        ("en", 3, 1, 0, 4),
        ("es_ES", 0, 0, 2, 0),
    ]
    assert broadcast.messages[0].sent_count == 3
    assert broadcast.messages[0].orphan_count == 4
    assert broadcast.messages[1].skipped_count == 2


# ---------------------------------------------------------------------------
# build_summary_view — the failed and complete DM variants
# ---------------------------------------------------------------------------


def test_build_summary_view_failed_variant():
    summary = make_summary(status=BroadcastStatus.FAILED, name="Camp", attempts=6, sent=1, failed=2, skipped=3)

    view = send_broadcasts.build_summary_view(summary, "en")

    expected = BroadcastOperatorMessages.SENDER_FAILED.get(
        lang="en", name="Camp", attempts=6, sent=1, failed=2, skipped=3
    )
    assert view.description == expected
    assert view.keyboard == []


def test_build_summary_view_complete_variant():
    breakdown = [LanguageBreakdown("en", 3, 1, 0)]
    summary = make_summary(
        status=BroadcastStatus.DONE, name="Camp", total=4, sent=3, failed=1, skipped=0, breakdown=breakdown
    )

    view = send_broadcasts.build_summary_view(summary, "en")

    expected_breakdown = FormattedText.join(
        "\n",
        [BroadcastOperatorMessages.SENDER_BREAKDOWN_LINE.get(lang="en", language="en", sent=3, failed=1, skipped=0)],
    )
    expected = BroadcastOperatorMessages.SENDER_COMPLETE_SUMMARY.get(
        lang="en", name="Camp", total=4, sent=3, failed=1, skipped=0, breakdown=expected_breakdown
    )
    assert view.description == expected


@pytest.mark.parametrize(
    "status", [BroadcastStatus.FAILED, BroadcastStatus.DONE], ids=["failed_variant", "complete_variant"]
)
def test_build_summary_view_appends_orphan_warning_when_present(status: BroadcastStatus):
    summary = make_summary(status=status, name="Camp", attempts=6, sent=1, failed=2, skipped=3, orphaned=5)

    view = send_broadcasts.build_summary_view(summary, "en")

    assert BroadcastOperatorMessages.SENDER_ORPHANED_WARNING.get(lang="en", orphaned=5).text in view.description.text


def test_build_summary_view_failed_omits_orphan_warning_when_none():
    summary = make_summary(status=BroadcastStatus.FAILED, name="Camp", attempts=6, sent=1, failed=2, skipped=3)

    view = send_broadcasts.build_summary_view(summary, "en")

    expected = BroadcastOperatorMessages.SENDER_FAILED.get(
        lang="en", name="Camp", attempts=6, sent=1, failed=2, skipped=3
    )
    assert view.description == expected


def test_build_summary_view_complete_omits_orphan_warning_when_none():
    breakdown = [LanguageBreakdown("en", 3, 1, 0)]
    summary = make_summary(
        status=BroadcastStatus.DONE, name="Camp", total=4, sent=3, failed=1, skipped=0, breakdown=breakdown
    )

    view = send_broadcasts.build_summary_view(summary, "en")

    expected_breakdown = FormattedText.join(
        "\n",
        [BroadcastOperatorMessages.SENDER_BREAKDOWN_LINE.get(lang="en", language="en", sent=3, failed=1, skipped=0)],
    )
    expected = BroadcastOperatorMessages.SENDER_COMPLETE_SUMMARY.get(
        lang="en", name="Camp", total=4, sent=3, failed=1, skipped=0, breakdown=expected_breakdown
    )
    assert view.description == expected


# ---------------------------------------------------------------------------
# build_breakdown_line — plain vs orphaned variant selection
# ---------------------------------------------------------------------------


def test_build_breakdown_line_uses_plain_variant_without_orphans():
    line = LanguageBreakdown(language="en", sent=3, failed=1, skipped=0, orphaned=0)

    result = send_broadcasts.build_breakdown_line(line, "en")

    expected = BroadcastOperatorMessages.SENDER_BREAKDOWN_LINE.get(
        lang="en", language="en", sent=3, failed=1, skipped=0
    )
    assert result == expected


def test_build_breakdown_line_uses_orphaned_variant_when_orphans_present():
    line = LanguageBreakdown(language="en", sent=3, failed=1, skipped=0, orphaned=2)

    result = send_broadcasts.build_breakdown_line(line, "en")

    expected = BroadcastOperatorMessages.SENDER_BREAKDOWN_LINE_WITH_ORPHANED.get(
        lang="en", language="en", sent=3, failed=1, skipped=0, orphaned=2
    )
    assert result == expected


# ---------------------------------------------------------------------------
# claim_next_broadcast
# ---------------------------------------------------------------------------


async def test_claim_next_broadcast_starts_queued(mock_session: MockDbSession):
    broadcast = create_broadcast(id=1, status=BroadcastStatus.QUEUED, attempts=0)
    script_exec(mock_session, Result(results=(broadcast,)))

    claimed = await send_broadcasts.claim_next_broadcast()

    assert claimed == ClaimedBroadcast(broadcast_id=1, attempts=1, terminal_failure=False)
    assert broadcast.status is BroadcastStatus.SENDING
    assert broadcast.sending_started_time is not None


async def test_claim_next_broadcast_resumes_sending_without_restamping(mock_session: MockDbSession):
    broadcast = create_broadcast(id=2, status=BroadcastStatus.SENDING, attempts=1)
    script_exec(mock_session, Result(results=(broadcast,)))

    claimed = await send_broadcasts.claim_next_broadcast()

    assert claimed == ClaimedBroadcast(broadcast_id=2, attempts=2, terminal_failure=False)
    assert broadcast.status is BroadcastStatus.SENDING
    assert broadcast.sending_started_time is None


async def test_claim_next_broadcast_flags_terminal_over_threshold(mock_session: MockDbSession):
    broadcast = create_broadcast(id=3, status=BroadcastStatus.SENDING, attempts=MAX_BROADCAST_ATTEMPTS)
    script_exec(mock_session, Result(results=(broadcast,)))

    claimed = await send_broadcasts.claim_next_broadcast()

    assert claimed is not None
    assert claimed.attempts == MAX_BROADCAST_ATTEMPTS + 1
    assert claimed.terminal_failure is True


async def test_claim_next_broadcast_returns_none_when_idle(mock_session: MockDbSession):
    script_exec(mock_session, Result())

    assert await send_broadcasts.claim_next_broadcast() is None


# ---------------------------------------------------------------------------
# load_broadcast_bodies / materialize_audience
# ---------------------------------------------------------------------------


async def test_load_broadcast_bodies_maps_language_to_html(mock_session: MockDbSession):
    messages = (BroadcastMessage(language="en", body_html="hi"), BroadcastMessage(language="es_ES", body_html="hola"))
    script_exec(mock_session, Result(results=messages))

    assert await send_broadcasts.load_broadcast_bodies(5) == {"en": "hi", "es_ES": "hola"}


async def test_materialize_audience_inserts_and_records_total(mock_session: MockDbSession):
    broadcast = create_broadcast(id=5)
    mock_session.get = mock.AsyncMock(return_value=broadcast)
    # count(before)=0 -> insert -> count(after)=4
    script_exec(mock_session, Result(results=(0,)), Result(), Result(results=(4,)))

    total, freshly_materialized = await send_broadcasts.materialize_audience(5, ["en"])

    assert (total, freshly_materialized) == (4, True)
    assert broadcast.total_recipients == 4


async def test_materialize_audience_is_idempotent_when_already_snapshotted(mock_session: MockDbSession):
    script_exec(mock_session, Result(results=(9,)))

    total, freshly_materialized = await send_broadcasts.materialize_audience(5, ["en"])

    assert (total, freshly_materialized) == (9, False)


# ---------------------------------------------------------------------------
# claim_pending_batch
# ---------------------------------------------------------------------------


async def test_claim_pending_batch_resolves_claimed_rows(mock_session: MockDbSession):
    member = create_member(1, 11, "en")
    script_exec(mock_session, Result(results=((101, 1, "en", 2),)), Result(results=(member,)))

    batch = await send_broadcasts.claim_pending_batch(5)

    assert [(item.delivery_id, item.user.db_id, item.language_sent, item.attempt_count) for item in batch] == [
        (101, 1, "en", 2)
    ]
    claim_query = mock_session.queries_executed[0]
    # The claim flips rows to IN_PROGRESS, not a terminal outcome — record_batch_outcomes resolves them later.
    assert "'in_progress'" in claim_query
    # Both PENDING and due RETRY_PENDING rows are eligible, and the claim bumps attempt_count atomically.
    assert "'pending'" in claim_query
    assert "'retry_pending'" in claim_query
    assert "attempt_count=(broadcast_deliveries.attempt_count + 1)" in claim_query
    # The claim also clears any stale retry timestamp so a re-claimed row that succeeds keeps no NULL-only field set.
    assert "next_attempt_time=NULL" in claim_query


async def test_claim_pending_batch_returns_empty_when_nothing_claimed(mock_session: MockDbSession):
    script_exec(mock_session, Result(results=()))

    assert await send_broadcasts.claim_pending_batch(5) == []


# ---------------------------------------------------------------------------
# record_batch_outcomes
# ---------------------------------------------------------------------------


async def test_record_batch_outcomes_marks_sent_and_deactivates_skipped(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions
):
    skipped_member = create_member(2, 22, status=UserStatus.MEMBER)
    outcomes = [DeliveryOutcome(101, 1, SENT), DeliveryOutcome(102, 2, SKIPPED)]
    # mark(sent) update, mark(skipped) update, deactivate select
    script_exec(mock_session, Result(), Result(), Result(results=(skipped_member,)))

    await send_broadcasts.record_batch_outcomes(batch_of(*outcomes), metrics_client)
    await metrics_client.flush()

    assert skipped_member.status is UserStatus.LEFT
    metrics.assert_emitted(name=MetricKey.INACTIVE_USER_SET, value=1)


async def test_record_batch_outcomes_skipped_only_deactivates_without_marking_sent(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions
):
    skipped_member = create_member(2, 22, status=UserStatus.MEMBER)
    # No SENT outcomes: only the skipped mark update and the deactivate select run.
    script_exec(mock_session, Result(), Result(results=(skipped_member,)))

    await send_broadcasts.record_batch_outcomes(batch_of(DeliveryOutcome(102, 2, SKIPPED)), metrics_client)
    await metrics_client.flush()

    assert skipped_member.status is UserStatus.LEFT
    metrics.assert_emitted(name=MetricKey.INACTIVE_USER_SET, value=1)


async def test_record_batch_outcomes_sent_only_touches_no_users(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions
):
    script_exec(mock_session, Result())

    await send_broadcasts.record_batch_outcomes(batch_of(DeliveryOutcome(101, 1, SENT)), metrics_client)
    await metrics_client.flush()

    assert mock_session.exec.await_count == 1
    metrics.assert_not_emitted(name=MetricKey.INACTIVE_USER_SET)


async def test_record_batch_outcomes_failed_only_marks_failed_without_deactivating(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions
):
    """The claim no longer pre-sets FAILED, so a FAILED outcome now needs its own explicit write."""
    script_exec(mock_session, Result())

    await send_broadcasts.record_batch_outcomes(batch_of(DeliveryOutcome(101, 1, FAILED)), metrics_client)
    await metrics_client.flush()

    assert mock_session.exec.await_count == 1
    assert "'failed'" in mock_session.queries_executed[0]
    metrics.assert_not_emitted(name=MetricKey.INACTIVE_USER_SET)


async def test_record_batch_outcomes_schedules_retry_pending_rows(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions
):
    next_attempt = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=60)
    script_exec(mock_session, Result())

    await send_broadcasts.record_batch_outcomes(
        batch_of(DeliveryOutcome(101, 1, RETRY_PENDING, next_attempt)), metrics_client
    )
    await metrics_client.flush()

    assert mock_session.exec.await_count == 1
    query = mock_session.queries_executed[0]
    assert "'retry_pending'" in query
    assert "next_attempt_time" in query
    metrics.assert_not_emitted(name=MetricKey.INACTIVE_USER_SET)


async def test_schedule_retries_writes_each_row_with_its_own_next_attempt_time(mock_session: MockDbSession):
    first_attempt = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=60)
    second_attempt = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=125)
    script_exec(mock_session, Result(), Result())

    await send_broadcasts.schedule_retries(
        mock_session,
        [DeliveryOutcome(101, 1, RETRY_PENDING, first_attempt), DeliveryOutcome(102, 2, RETRY_PENDING, second_attempt)],
    )

    assert mock_session.exec.await_count == 2
    assert all("'retry_pending'" in query for query in mock_session.queries_executed)


async def test_release_unattempted_reparks_and_restores_attempt_budget(mock_session: MockDbSession):
    script_exec(mock_session, Result())
    unattempted = [
        PendingDelivery(201, create_member(1, 11, "en"), "en", 2),
        PendingDelivery(202, create_member(2, 12, "en"), "en", 2),
    ]

    await send_broadcasts.release_unattempted(mock_session, unattempted, dt.timedelta(seconds=25))

    # One bulk UPDATE flips the rows back to RETRY_PENDING, sets next_attempt_time, and undoes the
    # claim's increment so their attempt budget is not spent on a non-attempt.
    assert mock_session.exec.await_count == 1
    query = mock_session.queries_executed[0]
    assert "UPDATE broadcast_deliveries" in query
    assert "'retry_pending'" in query
    assert "next_attempt_time" in query
    assert "attempt_count=(broadcast_deliveries.attempt_count - 1)" in query


async def test_record_batch_outcomes_releases_unattempted_rows_under_flood_control(
    mock_session: MockDbSession, metrics_client: MetricsClient
):
    # schedule_retries writes the flood-triggering row, then release_unattempted bulk-releases the
    # untried remainder in the same transaction.
    script_exec(mock_session, Result(), Result())
    result = BatchResult(
        outcomes=[DeliveryOutcome(101, 1, RETRY_PENDING, dt.datetime.now(dt.UTC))],
        flood_control=True,
        unattempted=[PendingDelivery(102, create_member(2, 12, "en"), "en", 1)],
        flood_backoff=dt.timedelta(seconds=25),
    )

    await send_broadcasts.record_batch_outcomes(result, metrics_client)
    await metrics_client.flush()

    assert mock_session.exec.await_count == 2
    release_query = mock_session.queries_executed[1]
    assert "attempt_count=(broadcast_deliveries.attempt_count - 1)" in release_query
    assert "'retry_pending'" in release_query


async def test_record_batch_outcomes_writes_all_three_terminal_groups_for_a_mixed_batch(
    mock_session: MockDbSession, metrics_client: MetricsClient, metrics: MetricAssertions
):
    skipped_member = create_member(3, 33, status=UserStatus.MEMBER)
    outcomes = [
        DeliveryOutcome(101, 1, SENT),
        DeliveryOutcome(102, 2, FAILED),
        DeliveryOutcome(103, 3, SKIPPED),
    ]
    # mark(sent), mark(failed), mark(skipped), then the deactivate-skipped-users select.
    script_exec(mock_session, Result(), Result(), Result(), Result(results=(skipped_member,)))

    await send_broadcasts.record_batch_outcomes(batch_of(*outcomes), metrics_client)
    await metrics_client.flush()

    assert mock_session.exec.await_count == 4
    queries = mock_session.queries_executed
    assert "'sent'" in queries[0]
    assert "'failed'" in queries[1]
    assert "'skipped_inactive'" in queries[2]
    assert skipped_member.status is UserStatus.LEFT
    metrics.assert_emitted(name=MetricKey.INACTIVE_USER_SET, value=1)


# ---------------------------------------------------------------------------
# fail_unattempted_deliveries
# ---------------------------------------------------------------------------


async def test_fail_unattempted_deliveries_marks_pending_rows_failed_scoped_to_broadcast(
    mock_session: MockDbSession,
):
    script_exec(mock_session, Result())

    await send_broadcasts.fail_unattempted_deliveries(mock_session, 5)

    assert mock_session.exec.await_count == 1
    query = mock_session.queries_executed[0]
    assert "UPDATE broadcast_deliveries" in query
    assert "'pending'" in query
    assert "'retry_pending'" in query
    assert "'failed'" in query
    assert "broadcast_id = 5" in query


# ---------------------------------------------------------------------------
# finalize_broadcast / purge_deliveries / load_operators
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rowcount, won", [(1, True), (0, False)], ids=["won", "lost"])
async def test_finalize_broadcast_rolls_up_counts(mock_session: MockDbSession, rowcount: int, won: bool):
    broadcast = create_broadcast(id=5, name="Camp", attempts=2)
    broadcast.total_recipients = 4
    broadcast.messages = [BroadcastMessage(language="en", body_html="hi")]
    mock_session.get = mock.AsyncMock(return_value=broadcast)
    # fail-unattempted UPDATE (a no-op here — nothing left PENDING), aggregate rows, then the
    # compare-and-swap UPDATE.
    script_exec(
        mock_session,
        Result(),
        Result(results=(("en", SENT, 3), ("en", FAILED, 1))),
        Result(rowcount=rowcount),
    )

    summary, won_transition = await send_broadcasts.finalize_broadcast(5, BroadcastStatus.DONE)

    assert won_transition is won
    assert (summary.sent, summary.failed, summary.skipped, summary.orphaned) == (3, 1, 0, 0)
    assert summary.total == 4
    assert summary.total == summary.sent + summary.failed + summary.skipped + summary.orphaned
    assert summary.name == "Camp"
    assert broadcast.sent_count == 3
    assert broadcast.orphan_count == 0
    assert broadcast.messages[0].sent_count == 3
    assert [line.language for line in summary.breakdown] == ["en"]


async def test_finalize_broadcast_rolls_in_progress_rows_into_orphans(mock_session: MockDbSession):
    """Rows still IN_PROGRESS at finalization are a worker crash, not a failure — kept separate."""
    broadcast = create_broadcast(id=6, name="Camp", attempts=1)
    broadcast.total_recipients = 5
    broadcast.messages = [BroadcastMessage(language="en", body_html="hi")]
    mock_session.get = mock.AsyncMock(return_value=broadcast)
    script_exec(
        mock_session,
        Result(),
        Result(results=(("en", SENT, 2), ("en", FAILED, 1), ("en", IN_PROGRESS, 2))),
        Result(rowcount=1),
    )

    summary, _ = await send_broadcasts.finalize_broadcast(6, BroadcastStatus.DONE)

    assert (summary.sent, summary.failed, summary.orphaned) == (2, 1, 2)
    assert summary.total == summary.sent + summary.failed + summary.skipped + summary.orphaned
    assert broadcast.orphan_count == 2
    assert broadcast.failed_count == 1
    assert broadcast.messages[0].orphan_count == 2


async def test_finalize_broadcast_terminal_failure_counts_never_attempted_deliveries_as_failed(
    mock_session: MockDbSession,
):
    """The terminal-failure path skips draining entirely, so every PENDING row here is
    never-attempted — `fail_unattempted_deliveries` converts them to genuine failures."""
    broadcast = create_broadcast(id=7, name="Camp", attempts=MAX_BROADCAST_ATTEMPTS + 1)
    broadcast.total_recipients = 3
    broadcast.messages = [BroadcastMessage(language="en", body_html="hi")]
    mock_session.get = mock.AsyncMock(return_value=broadcast)
    # fail-unattempted UPDATE (converts the never-attempted PENDING rows), then the aggregate
    # reflecting that conversion, then the compare-and-swap UPDATE.
    script_exec(
        mock_session,
        Result(),
        Result(results=(("en", FAILED, 3),)),
        Result(rowcount=1),
    )

    summary, _ = await send_broadcasts.finalize_broadcast(7, BroadcastStatus.FAILED)

    assert (summary.sent, summary.failed, summary.skipped, summary.orphaned) == (0, 3, 0, 0)
    assert summary.total == summary.sent + summary.failed + summary.skipped + summary.orphaned
    # fail_unattempted_deliveries ran before the aggregate — first exec call in the sequence.
    assert "'pending'" in mock_session.queries_executed[0]
    assert "'failed'" in mock_session.queries_executed[0]


async def test_purge_deliveries_issues_delete(mock_session: MockDbSession):
    script_exec(mock_session, Result())

    await send_broadcasts.purge_deliveries(5)

    assert mock_session.exec.await_count == 1
    assert "DELETE FROM broadcast_deliveries" in mock_session.queries_executed[0]


async def test_load_operators_returns_found_and_skips_missing(mock_session: MockDbSession):
    operator = create_member(1, 500, "en")
    # First tg id resolves to a user; the second has no row.
    script_exec(mock_session, Result(results=(operator,)), Result())

    operators = await send_broadcasts.load_operators([500, 999])

    assert operators == {500: operator}


# ---------------------------------------------------------------------------
# Orchestration — run / process_claimed_broadcast / send_all_pending / finalize_and_report / notify_operators
# ---------------------------------------------------------------------------


async def test_run_returns_early_when_nothing_claimed(
    api: MockApi, metrics_client: MetricsClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(send_broadcasts, "claim_next_broadcast", mock.AsyncMock(return_value=None))
    process = mock.AsyncMock()
    monkeypatch.setattr(send_broadcasts, "process_claimed_broadcast", process)

    await send_broadcasts.run(api, metrics_client, [1])

    process.assert_not_awaited()


async def test_run_delegates_to_processing_when_claimed(
    api: MockApi, metrics_client: MetricsClient, monkeypatch: pytest.MonkeyPatch
):
    claimed = ClaimedBroadcast(broadcast_id=5, attempts=1, terminal_failure=False)
    monkeypatch.setattr(send_broadcasts, "claim_next_broadcast", mock.AsyncMock(return_value=claimed))
    process = mock.AsyncMock()
    monkeypatch.setattr(send_broadcasts, "process_claimed_broadcast", process)

    await send_broadcasts.run(api, metrics_client, [1])

    process.assert_awaited_once_with(api, metrics_client, [1], claimed)


async def test_process_claimed_broadcast_terminal_failure_finalizes_failed(
    api: MockApi, metrics_client: MetricsClient, monkeypatch: pytest.MonkeyPatch
):
    finalize = mock.AsyncMock()
    monkeypatch.setattr(send_broadcasts, "finalize_and_report", finalize)
    claimed = ClaimedBroadcast(broadcast_id=5, attempts=MAX_BROADCAST_ATTEMPTS + 1, terminal_failure=True)

    await send_broadcasts.process_claimed_broadcast(api, metrics_client, [1], claimed)

    finalize.assert_awaited_once_with(api, metrics_client, [1], 5, BroadcastStatus.FAILED)


@pytest.mark.parametrize("freshly_materialized", [True, False], ids=["fresh", "resumed"])
async def test_process_claimed_broadcast_normal_path(
    api: MockApi,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
    monkeypatch: pytest.MonkeyPatch,
    freshly_materialized: bool,
):
    monkeypatch.setattr(send_broadcasts, "load_broadcast_bodies", mock.AsyncMock(return_value={"en": "hi"}))
    monkeypatch.setattr(send_broadcasts, "materialize_audience", mock.AsyncMock(return_value=(4, freshly_materialized)))
    send_all = mock.AsyncMock()
    monkeypatch.setattr(send_broadcasts, "send_all_pending", send_all)
    monkeypatch.setattr(send_broadcasts, "defer_for_pending_retries", mock.AsyncMock(return_value=False))
    finalize = mock.AsyncMock()
    monkeypatch.setattr(send_broadcasts, "finalize_and_report", finalize)
    claimed = ClaimedBroadcast(broadcast_id=5, attempts=1, terminal_failure=False)

    await send_broadcasts.process_claimed_broadcast(api, metrics_client, [1], claimed)
    await metrics_client.flush()

    send_all.assert_awaited_once_with(api, metrics_client, 5, {"en": "hi"})
    finalize.assert_awaited_once_with(api, metrics_client, [1], 5, BroadcastStatus.DONE)
    if freshly_materialized:
        metrics.assert_emitted(name=MetricKey.BROADCAST_MESSAGES_TO_SEND, value=4)
    else:
        metrics.assert_not_emitted(name=MetricKey.BROADCAST_MESSAGES_TO_SEND)


async def test_process_claimed_broadcast_defers_finalization_when_retries_pending(
    api: MockApi, metrics_client: MetricsClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(send_broadcasts, "load_broadcast_bodies", mock.AsyncMock(return_value={"en": "hi"}))
    monkeypatch.setattr(send_broadcasts, "materialize_audience", mock.AsyncMock(return_value=(4, False)))
    monkeypatch.setattr(send_broadcasts, "send_all_pending", mock.AsyncMock())
    monkeypatch.setattr(send_broadcasts, "defer_for_pending_retries", mock.AsyncMock(return_value=True))
    finalize = mock.AsyncMock()
    monkeypatch.setattr(send_broadcasts, "finalize_and_report", finalize)
    claimed = ClaimedBroadcast(broadcast_id=5, attempts=1, terminal_failure=False)

    await send_broadcasts.process_claimed_broadcast(api, metrics_client, [1], claimed)

    # Deliveries still await retry, so the broadcast is left SENDING for the next tick — no finalize.
    finalize.assert_not_awaited()


@pytest.mark.parametrize("unfinished, expected", [(0, False), (3, True)], ids=["all_done", "retries_pending"])
async def test_defer_for_pending_retries_resets_attempts_only_when_unfinished(
    monkeypatch: pytest.MonkeyPatch, unfinished: int, expected: bool
):
    monkeypatch.setattr(send_broadcasts, "count_unfinished_deliveries", mock.AsyncMock(return_value=unfinished))
    reset = mock.AsyncMock()
    monkeypatch.setattr(send_broadcasts, "reset_broadcast_attempts", reset)

    deferred = await send_broadcasts.defer_for_pending_retries(5)

    assert deferred is expected
    if expected:
        reset.assert_awaited_once_with(5)
    else:
        reset.assert_not_awaited()


async def test_count_unfinished_deliveries_counts_pending_and_retry_pending(mock_session: MockDbSession):
    script_exec(mock_session, Result(results=(4,)))

    assert await send_broadcasts.count_unfinished_deliveries(5) == 4
    query = mock_session.queries_executed[0]
    assert "'pending'" in query
    assert "'retry_pending'" in query
    assert "broadcast_id = 5" in query


async def test_reset_broadcast_attempts_issues_update(mock_session: MockDbSession):
    script_exec(mock_session, Result())

    await send_broadcasts.reset_broadcast_attempts(5)

    assert mock_session.exec.await_count == 1
    query = mock_session.queries_executed[0]
    assert "UPDATE broadcasts" in query
    assert "attempts=0" in query.replace(" ", "")


async def test_send_all_pending_drains_until_no_batch(
    api: MockApi, metrics_client: MetricsClient, metrics: MetricAssertions, monkeypatch: pytest.MonkeyPatch
):
    batch = [PendingDelivery(1, create_member(1, 11, "en"), "en", 1)]
    claim = mock.AsyncMock(side_effect=[batch, []])
    monkeypatch.setattr(send_broadcasts, "claim_pending_batch", claim)
    deliver = mock.AsyncMock(return_value=BatchResult(outcomes=[DeliveryOutcome(1, 1, SENT)], flood_control=False))
    monkeypatch.setattr(send_broadcasts, "deliver_batch", deliver)
    record = mock.AsyncMock()
    monkeypatch.setattr(send_broadcasts, "record_batch_outcomes", record)

    await send_broadcasts.send_all_pending(api, metrics_client, 5, {"en": "hi"})
    await metrics_client.flush()

    assert claim.await_count == 2
    deliver.assert_awaited_once()
    record.assert_awaited_once()
    # Nothing was a re-claimed retry (attempt_count 1), so the retried metric is not emitted.
    metrics.assert_not_emitted(name=MetricKey.BROADCAST_MESSAGES_RETRIED)


async def test_send_all_pending_stops_claiming_after_flood_control(
    api: MockApi, metrics_client: MetricsClient, monkeypatch: pytest.MonkeyPatch
):
    batch = [PendingDelivery(1, create_member(1, 11, "en"), "en", 1)]
    claim = mock.AsyncMock(side_effect=[batch, batch, []])
    monkeypatch.setattr(send_broadcasts, "claim_pending_batch", claim)
    deliver = mock.AsyncMock(
        return_value=BatchResult(
            outcomes=[DeliveryOutcome(1, 1, RETRY_PENDING, dt.datetime.now(dt.UTC))], flood_control=True
        )
    )
    monkeypatch.setattr(send_broadcasts, "deliver_batch", deliver)
    monkeypatch.setattr(send_broadcasts, "record_batch_outcomes", mock.AsyncMock())

    await send_broadcasts.send_all_pending(api, metrics_client, 5, {"en": "hi"})

    # The first batch tripped flood control, so the drain loop breaks — no second claim.
    assert claim.await_count == 1
    deliver.assert_awaited_once()


async def test_send_all_pending_emits_retried_metric_for_reclaimed_deliveries(
    api: MockApi, metrics_client: MetricsClient, metrics: MetricAssertions, monkeypatch: pytest.MonkeyPatch
):
    reclaimed = [
        PendingDelivery(1, create_member(1, 11, "en"), "en", 2),
        PendingDelivery(2, create_member(2, 12, "en"), "en", 3),
    ]
    monkeypatch.setattr(send_broadcasts, "claim_pending_batch", mock.AsyncMock(side_effect=[reclaimed, []]))
    monkeypatch.setattr(
        send_broadcasts,
        "deliver_batch",
        mock.AsyncMock(return_value=BatchResult(outcomes=[DeliveryOutcome(1, 1, SENT)], flood_control=False)),
    )
    monkeypatch.setattr(send_broadcasts, "record_batch_outcomes", mock.AsyncMock())

    await send_broadcasts.send_all_pending(api, metrics_client, 5, {"en": "hi"})
    await metrics_client.flush()

    metrics.assert_emitted(name=MetricKey.BROADCAST_MESSAGES_RETRIED, value=2)


@pytest.mark.parametrize("won_transition", [True, False], ids=["won", "lost"])
async def test_finalize_and_report_emits_counts_and_gates_notification(
    api: MockApi,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
    monkeypatch: pytest.MonkeyPatch,
    won_transition: bool,
):
    summary = make_summary(status=BroadcastStatus.DONE, sent=3, failed=1, skipped=2)
    monkeypatch.setattr(send_broadcasts, "finalize_broadcast", mock.AsyncMock(return_value=(summary, won_transition)))
    purge = mock.AsyncMock()
    monkeypatch.setattr(send_broadcasts, "purge_deliveries", purge)
    notify = mock.AsyncMock()
    monkeypatch.setattr(send_broadcasts, "notify_operators", notify)

    await send_broadcasts.finalize_and_report(api, metrics_client, [1], 5, BroadcastStatus.DONE)
    await metrics_client.flush()

    metrics.assert_emitted(name=MetricKey.BROADCAST_MESSAGES_SENT, value=3)
    metrics.assert_emitted(name=MetricKey.BROADCAST_MESSAGES_FAILED, value=1)
    metrics.assert_emitted(name=MetricKey.BROADCAST_MESSAGES_SKIPPED, value=2)
    metrics.assert_emitted(name=MetricKey.BROADCAST_MESSAGES_ORPHANED, value=0)
    purge.assert_awaited_once_with(5)
    if won_transition:
        notify.assert_awaited_once_with(api, [1], summary)
    else:
        notify.assert_not_awaited()


@pytest.mark.parametrize("orphaned", [0, 2], ids=["no_orphans", "with_orphans"])
async def test_finalize_and_report_emits_orphan_metric_and_warns_only_when_present(
    api: MockApi,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
    monkeypatch: pytest.MonkeyPatch,
    orphaned: int,
):
    summary = make_summary(status=BroadcastStatus.DONE, sent=3, failed=1, skipped=2, orphaned=orphaned)
    monkeypatch.setattr(send_broadcasts, "finalize_broadcast", mock.AsyncMock(return_value=(summary, True)))
    monkeypatch.setattr(send_broadcasts, "purge_deliveries", mock.AsyncMock())
    monkeypatch.setattr(send_broadcasts, "notify_operators", mock.AsyncMock())

    with capture_logs() as logs:
        await send_broadcasts.finalize_and_report(api, metrics_client, [1], 5, BroadcastStatus.DONE)
        await metrics_client.flush()

    metrics.assert_emitted(name=MetricKey.BROADCAST_MESSAGES_ORPHANED, value=orphaned)
    warnings = [entry for entry in logs if entry["event"] == "Broadcast finalized with orphaned deliveries"]
    assert len(warnings) == (1 if orphaned else 0)


async def test_notify_operators_continues_past_unreachable_operator(api: MockApi, monkeypatch: pytest.MonkeyPatch):
    reachable = create_member(1, 500, "en")
    unreachable = create_member(2, 501, "en")
    monkeypatch.setattr(
        send_broadcasts, "load_operators", mock.AsyncMock(return_value={500: reachable, 501: unreachable})
    )
    api.mock_method("send_message_to_user").side_effect = [None, InactiveUserInteraction(501, private=True)]
    summary = make_summary(status=BroadcastStatus.DONE)

    await send_broadcasts.notify_operators(api, [500, 501], summary)

    assert api.mock_method("send_message_to_user").await_count == 2
