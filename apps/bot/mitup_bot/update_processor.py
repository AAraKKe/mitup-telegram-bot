"""Update processor that serializes updates sharing the same (user, chat) key.

At a concurrency cap of 1 PTB's own semaphore already processes every update sequentially, so
the keyed locks are belt-and-braces; they become load-bearing when the configured cap
(`bot.concurrent_updates`) rises above 1 and different (user, chat) pairs start
overlapping. In-process locks are sufficient because the bot always runs a single uvicorn
worker (see the comment in MitupRuntime.run).
"""

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, override

import structlog
from telegram import Update
from telegram.ext import BaseUpdateProcessor

from mitup_bot.update_trace import (
    UPDATE_TRACE,
    UpdateTrace,
    clear_update_scoped_state,
    update_log_context,
    update_outcome,
)

LockKey = tuple[int | None, int | None]

log = structlog.get_logger(__name__)


def update_lock_key(update: object) -> LockKey | None:
    """Return the (user id, chat id) key guarding *update*'s per-user state, or None.

    Non-`telegram.Update` objects (anything can be fed into the update queue) and updates with
    neither an effective user nor an effective chat touch no per-user state (conversation
    states, `MitupUserData`), so there is nothing to serialize against.
    """
    if not isinstance(update, Update):
        return None
    user_id = update.effective_user.id if update.effective_user is not None else None
    chat_id = update.effective_chat.id if update.effective_chat is not None else None
    if user_id is None and chat_id is None:
        return None
    return (user_id, chat_id)


@dataclass
class _KeyedLock:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    waiters: int = 0


async def run_traced(coroutine: Awaitable[Any], *, serialized: bool, waiters: int):
    """Run one update's processing between the pair of lines that bound its trace.

    A `Processing update` with no matching `Finished processing update` is the hang signal, so the
    exit line is written from a `finally` and names the outcome even when the processing raised.
    """
    trace = UpdateTrace()
    token = UPDATE_TRACE.set(trace)
    log.info("Processing update", serialized=serialized, waiters=waiters)
    start = perf_counter()
    escaped = False
    try:
        await coroutine
    except BaseException:
        escaped = True
        raise
    finally:
        UPDATE_TRACE.reset(token)
        # The exit line is still this update's, so it is written before the boundary is swept.
        log.info(
            "Finished processing update",
            outcome=update_outcome(trace, escaped=escaped).value,
            handlers_run=trace.handlers_run,
            duration_ms=round((perf_counter() - start) * 1000, 1),
        )
        clear_update_scoped_state()


class PerUserUpdateProcessor(BaseUpdateProcessor):
    __slots__ = ("_locks",)

    def __init__(self, max_concurrent_updates: int):
        super().__init__(max_concurrent_updates)
        self._locks: dict[LockKey, _KeyedLock] = {}

    @override
    async def do_process_update(self, update: object, coroutine: Awaitable[Any]):
        # The correlation identity is bound here because this is the only point both run modes
        # reach, and it is bound before routing so an update no handler matches still names
        # itself. Everything the handler layer adds (flow, handler, callback data) is what
        # routing discovered, and routing has not happened yet.
        with structlog.contextvars.bound_contextvars(**update_log_context(update)):
            key = update_lock_key(update)
            if key is None:
                await run_traced(coroutine, serialized=False, waiters=1)
                return

            # The refcount mutations and dict lookups around each await run without an intervening
            # await point, so no other task can interleave with them: an entry is dropped exactly
            # when its last holder releases it, keeping the map bounded by in-flight updates.
            keyed_lock = self._locks.get(key)
            if keyed_lock is None:
                keyed_lock = _KeyedLock()
                self._locks[key] = keyed_lock
            keyed_lock.waiters += 1
            # Snapshot at arrival: the counter is shared and moves at every await, and the value is
            # read again after the lock is acquired. The number that explains this update's wait is
            # the queue depth it observed when it joined, not whatever the counter says later.
            waiters = keyed_lock.waiters
            try:
                if waiters > 1:
                    log.warning(
                        "Waiting for an in-flight update with the same key",
                        waiters=waiters,
                        reason="same_user_chat_key",
                    )
                async with keyed_lock.lock:
                    await run_traced(coroutine, serialized=True, waiters=waiters)
            finally:
                keyed_lock.waiters -= 1
                if keyed_lock.waiters == 0:
                    del self._locks[key]

    @override
    async def initialize(self):
        """No resources to allocate; locks are created lazily per update."""

    @override
    async def shutdown(self):
        """No resources to free; PTB drains in-flight updates before shutting down."""
