from unittest import mock

import pytest
from structlog.testing import capture_logs

from mitup_bot.events.broadcast import finalize
from mitup_bot.events.broadcast.types import MAX_BROADCAST_ATTEMPTS
from mitup_bot.models import BroadcastMessage
from mitup_bot.models.broadcasts import BroadcastDeliveryStatus, BroadcastStatus
from mitup_bot.monitoring import MetricKey, MetricsClient
from tests.events.broadcast.helpers import make_summary, script_exec
from tests.helpers import MockApi, MockDbSession, Result, create_broadcast
from tests.helpers.monitoring import MetricAssertions

SENT = BroadcastDeliveryStatus.SENT
FAILED = BroadcastDeliveryStatus.FAILED
SKIPPED = BroadcastDeliveryStatus.SKIPPED_INACTIVE
IN_PROGRESS = BroadcastDeliveryStatus.IN_PROGRESS


async def test_aggregate_delivery_counts_builds_language_status_map(mock_session: MockDbSession):
    script_exec(mock_session, Result(results=(("en", SENT, 3), ("es_ES", FAILED, 1))))

    counts = await finalize.aggregate_delivery_counts(mock_session, 5)

    assert counts == {("en", SENT): 3, ("es_ES", FAILED): 1}


@pytest.mark.parametrize("rowcount, expected", [(1, True), (0, False)], ids=["won", "lost"])
async def test_transition_to_terminal_reports_winner(mock_session: MockDbSession, rowcount: int, expected: bool):
    script_exec(mock_session, Result(rowcount=rowcount))

    assert await finalize.transition_to_terminal(mock_session, 5, BroadcastStatus.DONE) is expected


async def test_build_language_breakdown_maps_counts_onto_messages():
    broadcast = create_broadcast(id=5)
    broadcast.messages = [
        BroadcastMessage(language="en", body_html="hi"),
        BroadcastMessage(language="es_ES", body_html="hola"),
    ]
    counts = {("en", SENT): 3, ("en", FAILED): 1, ("es_ES", SKIPPED): 2, ("en", IN_PROGRESS): 4}

    breakdown = finalize.build_language_breakdown(broadcast, counts)

    assert [(line.language, line.sent, line.failed, line.skipped, line.orphaned) for line in breakdown] == [
        ("en", 3, 1, 0, 4),
        ("es_ES", 0, 0, 2, 0),
    ]
    assert broadcast.messages[0].sent_count == 3
    assert broadcast.messages[0].orphan_count == 4
    assert broadcast.messages[1].skipped_count == 2


async def test_fail_unattempted_deliveries_marks_pending_rows_failed_scoped_to_broadcast(
    mock_session: MockDbSession,
):
    script_exec(mock_session, Result(rowcount=2))

    flipped = await finalize.fail_unattempted_deliveries(mock_session, 5)

    # Returns how many rows it flipped so the caller can emit their delivery metric post-commit.
    assert flipped == 2
    assert mock_session.exec.await_count == 1
    query = mock_session.queries_executed[0]
    assert "UPDATE broadcast_deliveries" in query
    assert "'pending'" in query
    assert "'retry_pending'" in query
    assert "'failed'" in query
    assert "broadcast_id = 5" in query


@pytest.mark.parametrize("rowcount, won", [(1, True), (0, False)], ids=["won", "lost"])
async def test_finalize_broadcast_rolls_up_counts(mock_session: MockDbSession, rowcount: int, won: bool):
    broadcast = create_broadcast(id=5, name="Camp", attempts=2)
    broadcast.total_recipients = 4
    broadcast.messages = [BroadcastMessage(language="en", body_html="hi")]
    mock_session.get = mock.AsyncMock(return_value=broadcast)
    # fail-unattempted UPDATE (a no-op here — nothing left PENDING, 0 rows), aggregate rows, then
    # the compare-and-swap UPDATE.
    script_exec(
        mock_session,
        Result(rowcount=0),
        Result(results=(("en", SENT, 3), ("en", FAILED, 1))),
        Result(rowcount=rowcount),
    )

    summary, won_transition, bulk_failed = await finalize.finalize_broadcast(5, BroadcastStatus.DONE)

    assert won_transition is won
    # Nothing was left PENDING on the drained path, so no rows were bulk-failed.
    assert bulk_failed == 0
    assert (summary.sent, summary.failed, summary.skipped, summary.orphaned) == (3, 1, 0, 0)
    assert summary.total == 4
    assert summary.total == summary.sent + summary.failed + summary.skipped + summary.orphaned
    assert summary.name == "Camp"
    assert summary.broadcast_id == 5
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
        Result(rowcount=0),
        Result(results=(("en", SENT, 2), ("en", FAILED, 1), ("en", IN_PROGRESS, 2))),
        Result(rowcount=1),
    )

    summary, _, _ = await finalize.finalize_broadcast(6, BroadcastStatus.DONE)

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
    # fail-unattempted UPDATE (converts the 3 never-attempted PENDING rows), then the aggregate
    # reflecting that conversion, then the compare-and-swap UPDATE.
    script_exec(
        mock_session,
        Result(rowcount=3),
        Result(results=(("en", FAILED, 3),)),
        Result(rowcount=1),
    )

    summary, _, bulk_failed = await finalize.finalize_broadcast(7, BroadcastStatus.FAILED)

    assert (summary.sent, summary.failed, summary.skipped, summary.orphaned) == (0, 3, 0, 0)
    assert summary.total == summary.sent + summary.failed + summary.skipped + summary.orphaned
    # The 3 bulk-failed rows are surfaced so the caller can emit their delivery metric post-commit.
    assert bulk_failed == 3
    # fail_unattempted_deliveries ran before the aggregate — first exec call in the sequence.
    assert "'pending'" in mock_session.queries_executed[0]
    assert "'failed'" in mock_session.queries_executed[0]


async def test_purge_deliveries_issues_delete(mock_session: MockDbSession):
    script_exec(mock_session, Result())

    await finalize.purge_deliveries(5)

    assert mock_session.exec.await_count == 1
    assert "DELETE FROM broadcast_deliveries" in mock_session.queries_executed[0]


@pytest.mark.parametrize("won_transition", [True, False], ids=["won", "lost"])
async def test_finalize_and_report_gates_notification_on_won_transition(
    api: MockApi,
    metrics_client: MetricsClient,
    monkeypatch: pytest.MonkeyPatch,
    won_transition: bool,
):
    """Per-delivery telemetry is live during the drain, so finalization purges unconditionally and
    notifies only the call that won the terminal transition (no bulk-failed rows on this path)."""
    summary = make_summary(status=BroadcastStatus.DONE, sent=3, failed=1, skipped=2)
    monkeypatch.setattr(finalize, "finalize_broadcast", mock.AsyncMock(return_value=(summary, won_transition, 0)))
    purge = mock.AsyncMock()
    monkeypatch.setattr(finalize, "purge_deliveries", purge)
    notify = mock.AsyncMock()
    monkeypatch.setattr(finalize, "notify_operators", notify)

    await finalize.finalize_and_report(api, metrics_client, [1], 42, 5, BroadcastStatus.DONE)

    purge.assert_awaited_once_with(5)
    if won_transition:
        # The author id is passed through so the summary DM can prefer the author over the admins.
        notify.assert_awaited_once_with(api, [1], 42, summary)
    else:
        notify.assert_not_awaited()


async def test_finalize_and_report_emits_bulk_failed_delivery_metric(
    api: MockApi,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
    monkeypatch: pytest.MonkeyPatch,
):
    """On the terminal-failure path the bulk-failed rows never pass through the drain, so finalize
    emits their FAILED metric — count-valued (not one-hot), since there is no per-row outcome."""
    summary = make_summary(status=BroadcastStatus.FAILED, sent=0, failed=3, skipped=0)
    monkeypatch.setattr(finalize, "finalize_broadcast", mock.AsyncMock(return_value=(summary, True, 3)))
    monkeypatch.setattr(finalize, "purge_deliveries", mock.AsyncMock())
    monkeypatch.setattr(finalize, "notify_operators", mock.AsyncMock())

    await finalize.finalize_and_report(api, metrics_client, [1], 42, 5, BroadcastStatus.FAILED)
    await metrics_client.flush()

    metrics.assert_emitted(name=MetricKey.BROADCAST_DELIVERY_FAILED, value=3, properties={"broadcast_id": 5})


async def test_finalize_and_report_skips_bulk_failed_metric_when_none(
    api: MockApi,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
    monkeypatch: pytest.MonkeyPatch,
):
    summary = make_summary(status=BroadcastStatus.DONE, sent=3, failed=1, skipped=2)
    monkeypatch.setattr(finalize, "finalize_broadcast", mock.AsyncMock(return_value=(summary, True, 0)))
    monkeypatch.setattr(finalize, "purge_deliveries", mock.AsyncMock())
    monkeypatch.setattr(finalize, "notify_operators", mock.AsyncMock())

    await finalize.finalize_and_report(api, metrics_client, [1], 42, 5, BroadcastStatus.DONE)
    await metrics_client.flush()

    metrics.assert_not_emitted(name=MetricKey.BROADCAST_DELIVERY_FAILED)


@pytest.mark.parametrize("orphaned", [0, 2], ids=["no_orphans", "with_orphans"])
async def test_finalize_and_report_warns_only_when_orphans_present(
    api: MockApi,
    metrics_client: MetricsClient,
    monkeypatch: pytest.MonkeyPatch,
    orphaned: int,
):
    summary = make_summary(status=BroadcastStatus.DONE, sent=3, failed=1, skipped=2, orphaned=orphaned)
    monkeypatch.setattr(finalize, "finalize_broadcast", mock.AsyncMock(return_value=(summary, True, 0)))
    monkeypatch.setattr(finalize, "purge_deliveries", mock.AsyncMock())
    monkeypatch.setattr(finalize, "notify_operators", mock.AsyncMock())

    with capture_logs() as logs:
        await finalize.finalize_and_report(api, metrics_client, [1], 42, 5, BroadcastStatus.DONE)

    warnings = [entry for entry in logs if entry["event"] == "Broadcast finalized with orphaned deliveries"]
    assert len(warnings) == (1 if orphaned else 0)


# ---------------------------------------------------------------------------
# Terminal-outcome records
# ---------------------------------------------------------------------------


async def test_finalization_records_the_terminal_outcome(
    api: MockApi, metrics_client: MetricsClient, monkeypatch: pytest.MonkeyPatch
):
    """The closing beat of the trail: without it the outcome lives only in the operator DM and the
    DB row, neither of which a log query can reach."""
    summary = make_summary(status=BroadcastStatus.DONE, total=6, sent=3, failed=1, skipped=2)
    monkeypatch.setattr(finalize, "finalize_broadcast", mock.AsyncMock(return_value=(summary, True, 0)))
    monkeypatch.setattr(finalize, "purge_deliveries", mock.AsyncMock())
    monkeypatch.setattr(finalize, "notify_operators", mock.AsyncMock())

    with capture_logs() as logs:
        await finalize.finalize_and_report(api, metrics_client, [1], 42, 5, BroadcastStatus.DONE)

    finalized = next(entry for entry in logs if entry["event"] == "Broadcast finalized")
    assert finalized["status"] == BroadcastStatus.DONE.value
    assert (finalized["total"], finalized["sent"], finalized["failed"], finalized["skipped"]) == (6, 3, 1, 2)
    assert finalized["won_transition"] is True
    assert finalized["reason"] == "drained"


async def test_the_losing_side_of_a_concurrent_finalization_is_recorded(
    api: MockApi, metrics_client: MetricsClient, monkeypatch: pytest.MonkeyPatch
):
    """Two workers can both drain the same broadcast and both reach finalization; the loser leaves
    no trace in the DB row, so without this line a double finalization is undetectable."""
    summary = make_summary(status=BroadcastStatus.DONE)
    monkeypatch.setattr(finalize, "finalize_broadcast", mock.AsyncMock(return_value=(summary, False, 0)))
    monkeypatch.setattr(finalize, "purge_deliveries", mock.AsyncMock())
    monkeypatch.setattr(finalize, "notify_operators", mock.AsyncMock())

    with capture_logs() as logs:
        await finalize.finalize_and_report(api, metrics_client, [1], 42, 5, BroadcastStatus.DONE)

    lost = next(entry for entry in logs if entry["event"] == "Broadcast finalization already performed")
    assert lost["won_transition"] is False
    assert lost["reason"] == "lost_terminal_cas"
    assert not [entry for entry in logs if entry["event"] == "Broadcast finalized"]


async def test_terminal_failure_names_the_attempt_threshold_as_its_cause(
    api: MockApi, metrics_client: MetricsClient, monkeypatch: pytest.MonkeyPatch
):
    summary = make_summary(status=BroadcastStatus.FAILED, attempts=MAX_BROADCAST_ATTEMPTS + 1)
    monkeypatch.setattr(finalize, "finalize_broadcast", mock.AsyncMock(return_value=(summary, True, 0)))
    monkeypatch.setattr(finalize, "purge_deliveries", mock.AsyncMock())
    monkeypatch.setattr(finalize, "notify_operators", mock.AsyncMock())

    with capture_logs() as logs:
        await finalize.finalize_and_report(api, metrics_client, [1], 42, 5, BroadcastStatus.FAILED)

    finalized = next(entry for entry in logs if entry["event"] == "Broadcast finalized")
    assert finalized["reason"] == "attempt_threshold_exceeded"


async def test_bulk_failed_rows_state_why_they_were_recorded_as_non_deliveries(mock_session: MockDbSession):
    """On the terminal-failure path this bulk flip is the reason thousands of recipients end up
    recorded as failures, so it has to state that cause rather than just how many."""
    script_exec(mock_session, Result(rowcount=2))

    with capture_logs() as logs:
        await finalize.fail_unattempted_deliveries(mock_session, 5)

    bulk = next(entry for entry in logs if entry["event"] == "Broadcast deliveries bulk failed")
    assert bulk["log_level"] == "warning"
    assert bulk["broadcast_id"] == 5
    assert bulk["count"] == 2
    assert bulk["reason"] == "finalized_with_undelivered_rows"


async def test_a_no_op_bulk_fail_stays_silent(mock_session: MockDbSession):
    """The normal drained path flips nothing, and a zero-row line every run would be pure noise."""
    script_exec(mock_session, Result(rowcount=0))

    with capture_logs() as logs:
        await finalize.fail_unattempted_deliveries(mock_session, 5)

    assert not [entry for entry in logs if entry["event"] == "Broadcast deliveries bulk failed"]


async def test_purge_records_how_much_audit_trail_it_destroyed(mock_session: MockDbSession):
    """The per-recipient audit table is destroyed here — the last chance to reconcile the delivery
    lines against DB state."""
    script_exec(mock_session, Result(rowcount=4))

    with capture_logs() as logs:
        await finalize.purge_deliveries(5)

    purged = next(entry for entry in logs if entry["event"] == "Broadcast deliveries purged")
    assert purged["broadcast_id"] == 5
    assert purged["count"] == 4
    assert purged["reason"] == "broadcast_terminal"
