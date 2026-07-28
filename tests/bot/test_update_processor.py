import asyncio
import datetime
from collections.abc import MutableMapping
from typing import Any

import pytest
import structlog
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs
from telegram import Chat, InlineQuery, Message, Update, User

from mitup_bot import db
from mitup_bot.update_processor import LockKey, PerUserUpdateProcessor, update_lock_key
from mitup_bot.update_trace import record_handler_invocation

TEST_DATE = datetime.datetime(2024, 7, 1, tzinfo=datetime.UTC)

# Generous guard against deadlocks: a regression that serializes independent keys (or never
# releases a lock) fails the test quickly instead of hanging the suite.
DEADLOCK_TIMEOUT = 5


def message_update(update_id: int, user_id: int, chat_id: int) -> Update:
    """An update carrying both an effective user and an effective chat."""
    return Update(
        update_id,
        message=Message(
            message_id=update_id,
            date=TEST_DATE,
            chat=Chat(id=chat_id, type="private"),
            from_user=User(id=user_id, first_name="Test", is_bot=False),
        ),
    )


def inline_query_update(update_id: int, user_id: int) -> Update:
    """An update with an effective user but no effective chat."""
    return Update(
        update_id,
        inline_query=InlineQuery(
            id=str(update_id),
            from_user=User(id=user_id, first_name="Test", is_bot=False),
            query="",
            offset="",
        ),
    )


def chat_only_update(update_id: int, chat_id: int) -> Update:
    """A synthetic update with an effective chat but no effective user.

    The bot does not receive such updates today; this only covers the defensive chat-only
    fallback in update_lock_key.
    """
    return Update(
        update_id,
        message=Message(
            message_id=update_id,
            date=TEST_DATE,
            chat=Chat(id=chat_id, type="private"),
            from_user=None,
        ),
    )


# --- update_lock_key ---


@pytest.mark.parametrize(
    "update, expected",
    [
        (message_update(1, user_id=1, chat_id=10), (1, 10)),
        (inline_query_update(1, user_id=1), (1, None)),
        (chat_only_update(1, chat_id=10), (None, 10)),
        (Update(1), None),
        (object(), None),
    ],
    ids=["user-and-chat", "user-only", "chat-only", "neither", "non-update"],
)
def test_update_lock_key(update: object, expected: LockKey | None):
    assert update_lock_key(update) == expected


# --- Serialization / concurrency properties ---


async def test_same_key_updates_never_interleave():
    """A second update with the same (user, chat) key must not start while the first is in
    flight, even though the processor's cap would allow both to run concurrently."""
    processor = PerUserUpdateProcessor(4)
    log: list[str] = []
    first_running = asyncio.Event()
    release_first = asyncio.Event()

    async def first():
        log.append("first:start")
        first_running.set()
        await release_first.wait()
        log.append("first:end")

    async def second():
        log.append("second:start")
        log.append("second:end")

    first_task = asyncio.create_task(processor.do_process_update(message_update(1, user_id=1, chat_id=10), first()))
    async with asyncio.timeout(DEADLOCK_TIMEOUT):
        await first_running.wait()
    second_task = asyncio.create_task(processor.do_process_update(message_update(2, user_id=1, chat_id=10), second()))
    # Yield a few times: if the lock did not serialize, second() would run to completion here.
    for _ in range(5):
        await asyncio.sleep(0)
    assert log == ["first:start"]

    release_first.set()
    async with asyncio.timeout(DEADLOCK_TIMEOUT):
        await asyncio.gather(first_task, second_task)
    assert log == ["first:start", "first:end", "second:start", "second:end"]


@pytest.mark.parametrize(
    "key_a, key_b",
    [
        ((1, 10), (2, 10)),
        ((1, 10), (1, 20)),
        ((1, 10), (2, 20)),
    ],
    ids=["different-user-same-chat", "same-user-different-chat", "both-different"],
)
async def test_different_key_updates_overlap(key_a: tuple[int, int], key_b: tuple[int, int]):
    """Updates with distinct keys genuinely interleave: each coroutine only completes if the
    other made progress while it was still in flight, so serialization would deadlock."""
    processor = PerUserUpdateProcessor(4)
    first_running = asyncio.Event()
    second_running = asyncio.Event()

    async def first():
        first_running.set()
        await second_running.wait()

    async def second():
        await first_running.wait()
        second_running.set()

    async with asyncio.timeout(DEADLOCK_TIMEOUT):
        await asyncio.gather(
            processor.do_process_update(message_update(1, user_id=key_a[0], chat_id=key_a[1]), first()),
            processor.do_process_update(message_update(2, user_id=key_b[0], chat_id=key_b[1]), second()),
        )


async def test_none_key_update_processes_without_lock_state():
    """An update with no lock key runs its coroutine directly and never touches the lock map."""
    processor = PerUserUpdateProcessor(4)
    locks_seen_mid_processing: list[dict[LockKey, object]] = []

    async def probe():
        locks_seen_mid_processing.append(dict(processor._locks))

    await processor.do_process_update(Update(1), probe())

    assert locks_seen_mid_processing == [{}]
    assert processor._locks == {}


# --- Lock map cleanup ---


async def test_locks_drain_after_contended_same_key_batch():
    processor = PerUserUpdateProcessor(4)
    holder_running = asyncio.Event()
    release_holder = asyncio.Event()

    async def holder():
        holder_running.set()
        await release_holder.wait()

    async def noop(): ...

    tasks = [
        asyncio.create_task(processor.do_process_update(message_update(i, user_id=1, chat_id=10), coroutine))
        for i, coroutine in enumerate((holder(), noop(), noop()), start=1)
    ]
    async with asyncio.timeout(DEADLOCK_TIMEOUT):
        await holder_running.wait()
    # Yield so both trailing tasks register on the contended lock before we inspect it.
    for _ in range(5):
        await asyncio.sleep(0)
    assert processor._locks[(1, 10)].waiters == 3  # holder + 2 queued

    release_holder.set()
    async with asyncio.timeout(DEADLOCK_TIMEOUT):
        await asyncio.gather(*tasks)
    assert processor._locks == {}


async def test_exception_releases_lock_and_drains_map():
    """A raising coroutine propagates its exception, but still releases the lock so queued
    same-key updates run, and its entry is removed from the map once the last holder exits."""
    processor = PerUserUpdateProcessor(4)
    log: list[str] = []
    boom_running = asyncio.Event()
    release_boom = asyncio.Event()

    async def boom():
        boom_running.set()
        await release_boom.wait()
        raise ValueError("boom")

    async def follower():
        log.append("follower")

    failing_task = asyncio.create_task(processor.do_process_update(message_update(1, user_id=1, chat_id=10), boom()))
    async with asyncio.timeout(DEADLOCK_TIMEOUT):
        await boom_running.wait()
    trailing_task = asyncio.create_task(
        processor.do_process_update(message_update(2, user_id=1, chat_id=10), follower())
    )
    for _ in range(5):
        await asyncio.sleep(0)
    assert log == []  # follower is queued behind the failing holder

    release_boom.set()
    with pytest.raises(ValueError, match="boom"):
        await failing_task
    async with asyncio.timeout(DEADLOCK_TIMEOUT):
        await trailing_task

    assert log == ["follower"]
    assert processor._locks == {}


# --- The update trace ---


def trace_lines(logs: list[MutableMapping[str, Any]]) -> tuple[MutableMapping[str, Any], MutableMapping[str, Any]]:
    """The entry and exit line of one processed update."""
    entry = next(line for line in logs if line["event"] == "Processing update")
    exit_line = next(line for line in logs if line["event"] == "Finished processing update")
    return entry, exit_line


async def test_trace_lines_bracket_a_routed_update():
    """The pair carries the correlation identity bound before routing, and the exit line reports
    what the handler layer recorded on the way through."""
    processor = PerUserUpdateProcessor(4)

    async def routed():
        record_handler_invocation(faulted=False)

    # merge_contextvars must run inside capture_logs so the bound identity lands on the events;
    # capture_logs otherwise disables the configured processor chain.
    with capture_logs(processors=[merge_contextvars]) as logs:
        await processor.do_process_update(message_update(7, user_id=1, chat_id=10), routed())

    entry, exit_line = trace_lines(logs)
    assert entry["update_id"] == 7
    assert entry["update_type"] == "message"
    assert entry["tg_user_id"] == 1
    assert entry["chat_id"] == 10
    assert entry["serialized"] is True
    assert exit_line["outcome"] == "handled"
    assert exit_line["handlers_run"] == 1
    assert exit_line["update_id"] == 7


async def test_an_update_no_handler_matched_is_reported_as_unrouted():
    """The line that answers "I pressed it and nothing happened": today such an update produces
    no record at all."""
    processor = PerUserUpdateProcessor(4)

    async def matched_nothing(): ...

    with capture_logs() as logs:
        await processor.do_process_update(message_update(7, user_id=1, chat_id=10), matched_nothing())

    _, exit_line = trace_lines(logs)
    assert exit_line["outcome"] == "unrouted"
    assert exit_line["handlers_run"] == 0


async def test_a_handler_fault_is_reported_as_failed():
    """PTB swallows handler exceptions into its own error plane, so the outcome is only knowable
    from what the wrapped invocation recorded."""
    processor = PerUserUpdateProcessor(4)

    async def faulted():
        record_handler_invocation(faulted=True)

    with capture_logs() as logs:
        await processor.do_process_update(message_update(7, user_id=1, chat_id=10), faulted())

    _, exit_line = trace_lines(logs)
    assert exit_line["outcome"] == "failed"
    assert exit_line["handlers_run"] == 1


async def test_an_escaping_exception_still_closes_the_trace():
    processor = PerUserUpdateProcessor(4)

    async def boom():
        raise ValueError("boom")

    with capture_logs() as logs, pytest.raises(ValueError, match="boom"):
        await processor.do_process_update(message_update(7, user_id=1, chat_id=10), boom())

    _, exit_line = trace_lines(logs)
    assert exit_line["outcome"] == "failed"


async def test_the_unserialized_short_circuit_traces_identically():
    """An update with no lock key bypasses serialization; it must not bypass the trace."""
    processor = PerUserUpdateProcessor(4)

    async def probe(): ...

    with capture_logs() as logs:
        await processor.do_process_update(Update(7), probe())

    entry, exit_line = trace_lines(logs)
    assert entry["serialized"] is False
    assert exit_line["outcome"] == "unrouted"


async def test_contention_on_the_same_key_is_warned_once():
    """Lock waits are invisible latency today: only the update that has to queue says so."""
    processor = PerUserUpdateProcessor(4)
    holder_running = asyncio.Event()
    release_holder = asyncio.Event()

    async def holder():
        holder_running.set()
        await release_holder.wait()

    async def follower(): ...

    with capture_logs() as logs:
        holder_task = asyncio.create_task(
            processor.do_process_update(message_update(1, user_id=1, chat_id=10), holder())
        )
        async with asyncio.timeout(DEADLOCK_TIMEOUT):
            await holder_running.wait()
        follower_task = asyncio.create_task(
            processor.do_process_update(message_update(2, user_id=1, chat_id=10), follower())
        )
        for _ in range(5):
            await asyncio.sleep(0)
        release_holder.set()
        async with asyncio.timeout(DEADLOCK_TIMEOUT):
            await asyncio.gather(holder_task, follower_task)

    contention = [line for line in logs if line["event"] == "Waiting for an in-flight update with the same key"]
    assert len(contention) == 1
    assert contention[0]["waiters"] == 2
    assert contention[0]["reason"] == "same_user_chat_key"


async def test_state_one_update_bound_never_reaches_the_next():
    """At the configured cap of 1 PTB awaits every update inside its own fetcher task
    (`Application.__update_fetcher`), so one context serves the whole process: a meeting a guard
    resolved, and the phase a failed critical section left marked, would otherwise be read as the
    next update's. This drives the two updates the way PTB does, in one task."""
    processor = PerUserUpdateProcessor(1)

    async def acted_on_a_meeting():
        structlog.contextvars.bind_contextvars(meeting_id=15467)
        db.WRITE_STATE.set(db.WriteState(db.WritePhase.BODY, committed=False))

    async def read_only():
        structlog.get_logger("mitup_bot").info("a later, unrelated update")

    with capture_logs(processors=[merge_contextvars]) as logs:
        await processor.do_process_update(message_update(1, user_id=1, chat_id=10), acted_on_a_meeting())
        await processor.do_process_update(message_update(2, user_id=2, chat_id=20), read_only())

    # The first update's own exit line still names the meeting it acted on.
    closed = [line for line in logs if line["event"] == "Finished processing update"]
    assert closed[0]["meeting_id"] == 15467

    later = next(line for line in logs if line["event"] == "a later, unrelated update")
    assert "meeting_id" not in later
    assert db.current_write_state() is None
