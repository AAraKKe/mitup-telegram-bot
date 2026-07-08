from unittest import mock

import pytest
from structlog.testing import capture_logs

from mitup_bot.cli.broadcast import finalize
from mitup_bot.cli.broadcast.types import MAX_BROADCAST_ATTEMPTS
from mitup_bot.models import BroadcastMessage
from mitup_bot.models.broadcasts import BroadcastDeliveryStatus, BroadcastStatus
from mitup_bot.monitoring import MetricKey, MetricsClient
from tests.cli.broadcast.helpers import make_summary, script_exec
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
    script_exec(mock_session, Result())

    await finalize.fail_unattempted_deliveries(mock_session, 5)

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
    # fail-unattempted UPDATE (a no-op here — nothing left PENDING), aggregate rows, then the
    # compare-and-swap UPDATE.
    script_exec(
        mock_session,
        Result(),
        Result(results=(("en", SENT, 3), ("en", FAILED, 1))),
        Result(rowcount=rowcount),
    )

    summary, won_transition = await finalize.finalize_broadcast(5, BroadcastStatus.DONE)

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

    summary, _ = await finalize.finalize_broadcast(6, BroadcastStatus.DONE)

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

    summary, _ = await finalize.finalize_broadcast(7, BroadcastStatus.FAILED)

    assert (summary.sent, summary.failed, summary.skipped, summary.orphaned) == (0, 3, 0, 0)
    assert summary.total == summary.sent + summary.failed + summary.skipped + summary.orphaned
    # fail_unattempted_deliveries ran before the aggregate — first exec call in the sequence.
    assert "'pending'" in mock_session.queries_executed[0]
    assert "'failed'" in mock_session.queries_executed[0]


async def test_purge_deliveries_issues_delete(mock_session: MockDbSession):
    script_exec(mock_session, Result())

    await finalize.purge_deliveries(5)

    assert mock_session.exec.await_count == 1
    assert "DELETE FROM broadcast_deliveries" in mock_session.queries_executed[0]


@pytest.mark.parametrize("won_transition", [True, False], ids=["won", "lost"])
async def test_finalize_and_report_emits_counts_and_gates_notification(
    api: MockApi,
    metrics_client: MetricsClient,
    metrics: MetricAssertions,
    monkeypatch: pytest.MonkeyPatch,
    won_transition: bool,
):
    summary = make_summary(status=BroadcastStatus.DONE, sent=3, failed=1, skipped=2)
    monkeypatch.setattr(finalize, "finalize_broadcast", mock.AsyncMock(return_value=(summary, won_transition)))
    purge = mock.AsyncMock()
    monkeypatch.setattr(finalize, "purge_deliveries", purge)
    notify = mock.AsyncMock()
    monkeypatch.setattr(finalize, "notify_operators", notify)

    await finalize.finalize_and_report(api, metrics_client, [1], 5, BroadcastStatus.DONE)
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
    monkeypatch.setattr(finalize, "finalize_broadcast", mock.AsyncMock(return_value=(summary, True)))
    monkeypatch.setattr(finalize, "purge_deliveries", mock.AsyncMock())
    monkeypatch.setattr(finalize, "notify_operators", mock.AsyncMock())

    with capture_logs() as logs:
        await finalize.finalize_and_report(api, metrics_client, [1], 5, BroadcastStatus.DONE)
        await metrics_client.flush()

    metrics.assert_emitted(name=MetricKey.BROADCAST_MESSAGES_ORPHANED, value=orphaned)
    warnings = [entry for entry in logs if entry["event"] == "Broadcast finalized with orphaned deliveries"]
    assert len(warnings) == (1 if orphaned else 0)
