from collections.abc import Sequence
from typing import Any, cast
from unittest import mock

from sqlalchemy import Row

from mitup_bot.cli.broadcast import claiming
from mitup_bot.cli.broadcast.types import MAX_BROADCAST_ATTEMPTS, ClaimedBroadcast
from mitup_bot.models import BroadcastMessage
from mitup_bot.models.broadcasts import BroadcastStatus
from tests.cli.broadcast.helpers import script_exec
from tests.helpers import MockDbSession, Result, create_broadcast, create_member


async def test_count_deliveries_reads_scalar(mock_session: MockDbSession):
    script_exec(mock_session, Result(results=(7,)))

    assert await claiming.count_deliveries(mock_session, 5) == 7


async def test_resolve_claimed_recipients_pairs_users_to_deliveries(mock_session: MockDbSession):
    first = create_member(1, 11, "en")
    second = create_member(2, 12, "es_ES")
    script_exec(mock_session, Result(results=(first, second)))

    claimed = cast(Sequence[Row[Any]], [(101, 1, "en", 1), (102, 2, "es_ES", 3)])
    pending = await claiming.resolve_claimed_recipients(mock_session, claimed)

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
    pending = await claiming.resolve_claimed_recipients(mock_session, claimed)

    assert [item.delivery_id for item in pending] == [101, 102, 103]
    assert [item.user.db_id for item in pending] == [1, 2, 3]
    assert [item.attempt_count for item in pending] == [2, 3, 1]


async def test_claim_next_broadcast_starts_queued(mock_session: MockDbSession):
    broadcast = create_broadcast(id=1, status=BroadcastStatus.QUEUED, attempts=0)
    script_exec(mock_session, Result(results=(broadcast,)))

    claimed = await claiming.claim_next_broadcast()

    assert claimed == ClaimedBroadcast(broadcast_id=1, attempts=1, terminal_failure=False)
    assert broadcast.status is BroadcastStatus.SENDING
    assert broadcast.sending_started_time is not None


async def test_claim_next_broadcast_resumes_sending_without_restamping(mock_session: MockDbSession):
    broadcast = create_broadcast(id=2, status=BroadcastStatus.SENDING, attempts=1)
    script_exec(mock_session, Result(results=(broadcast,)))

    claimed = await claiming.claim_next_broadcast()

    assert claimed == ClaimedBroadcast(broadcast_id=2, attempts=2, terminal_failure=False)
    assert broadcast.status is BroadcastStatus.SENDING
    assert broadcast.sending_started_time is None


async def test_claim_next_broadcast_flags_terminal_over_threshold(mock_session: MockDbSession):
    broadcast = create_broadcast(id=3, status=BroadcastStatus.SENDING, attempts=MAX_BROADCAST_ATTEMPTS)
    script_exec(mock_session, Result(results=(broadcast,)))

    claimed = await claiming.claim_next_broadcast()

    assert claimed is not None
    assert claimed.attempts == MAX_BROADCAST_ATTEMPTS + 1
    assert claimed.terminal_failure is True


async def test_claim_next_broadcast_returns_none_when_idle(mock_session: MockDbSession):
    script_exec(mock_session, Result())

    assert await claiming.claim_next_broadcast() is None


async def test_load_broadcast_bodies_maps_language_to_html(mock_session: MockDbSession):
    messages = (BroadcastMessage(language="en", body_html="hi"), BroadcastMessage(language="es_ES", body_html="hola"))
    script_exec(mock_session, Result(results=messages))

    assert await claiming.load_broadcast_bodies(5) == {"en": "hi", "es_ES": "hola"}


async def test_materialize_audience_inserts_and_records_total(mock_session: MockDbSession):
    broadcast = create_broadcast(id=5)
    mock_session.get = mock.AsyncMock(return_value=broadcast)
    # count(before)=0 -> insert -> count(after)=4
    script_exec(mock_session, Result(results=(0,)), Result(), Result(results=(4,)))

    total, freshly_materialized = await claiming.materialize_audience(5, ["en"])

    assert (total, freshly_materialized) == (4, True)
    assert broadcast.total_recipients == 4


async def test_materialize_audience_is_idempotent_when_already_snapshotted(mock_session: MockDbSession):
    script_exec(mock_session, Result(results=(9,)))

    total, freshly_materialized = await claiming.materialize_audience(5, ["en"])

    assert (total, freshly_materialized) == (9, False)


async def test_claim_pending_batch_resolves_claimed_rows(mock_session: MockDbSession):
    member = create_member(1, 11, "en")
    script_exec(mock_session, Result(results=((101, 1, "en", 2),)), Result(results=(member,)))

    batch = await claiming.claim_pending_batch(5)

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

    assert await claiming.claim_pending_batch(5) == []


async def test_count_unfinished_deliveries_counts_pending_and_retry_pending(mock_session: MockDbSession):
    script_exec(mock_session, Result(results=(4,)))

    assert await claiming.count_unfinished_deliveries(5) == 4
    query = mock_session.queries_executed[0]
    assert "'pending'" in query
    assert "'retry_pending'" in query
    assert "broadcast_id = 5" in query


async def test_reset_broadcast_attempts_issues_update(mock_session: MockDbSession):
    script_exec(mock_session, Result())

    await claiming.reset_broadcast_attempts(5)

    assert mock_session.exec.await_count == 1
    query = mock_session.queries_executed[0]
    assert "UPDATE broadcasts" in query
    assert "attempts=0" in query.replace(" ", "")
