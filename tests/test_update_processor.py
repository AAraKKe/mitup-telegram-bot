import asyncio
import datetime

import pytest
from telegram import Chat, InlineQuery, Message, Update, User

from mitup_bot.update_processor import LockKey, PerUserUpdateProcessor, update_lock_key

TEST_DATE = datetime.datetime(2024, 7, 1, tzinfo=datetime.UTC)

# Generous guard against deadlocks: a regression that serializes independent keys (or never
# releases a lock) fails the test quickly instead of hanging the suite.
DEADLOCK_TIMEOUT = 5


def _message_update(update_id: int, user_id: int, chat_id: int) -> Update:
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


def _inline_query_update(update_id: int, user_id: int) -> Update:
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


def _chat_only_update(update_id: int, chat_id: int) -> Update:
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
        (_message_update(1, user_id=1, chat_id=10), (1, 10)),
        (_inline_query_update(1, user_id=1), (1, None)),
        (_chat_only_update(1, chat_id=10), (None, 10)),
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

    first_task = asyncio.create_task(processor.do_process_update(_message_update(1, user_id=1, chat_id=10), first()))
    async with asyncio.timeout(DEADLOCK_TIMEOUT):
        await first_running.wait()
    second_task = asyncio.create_task(processor.do_process_update(_message_update(2, user_id=1, chat_id=10), second()))
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
            processor.do_process_update(_message_update(1, user_id=key_a[0], chat_id=key_a[1]), first()),
            processor.do_process_update(_message_update(2, user_id=key_b[0], chat_id=key_b[1]), second()),
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
        asyncio.create_task(processor.do_process_update(_message_update(i, user_id=1, chat_id=10), coroutine))
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

    failing_task = asyncio.create_task(processor.do_process_update(_message_update(1, user_id=1, chat_id=10), boom()))
    async with asyncio.timeout(DEADLOCK_TIMEOUT):
        await boom_running.wait()
    trailing_task = asyncio.create_task(
        processor.do_process_update(_message_update(2, user_id=1, chat_id=10), follower())
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
