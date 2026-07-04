"""Update processor that serializes updates sharing the same (user, chat) key.

At a concurrency cap of 1 PTB's own semaphore already processes every update sequentially, so
the keyed locks are belt-and-braces; they become load-bearing when the configured cap
(`bot.concurrent_updates`, #190) rises above 1 and different (user, chat) pairs start
overlapping. In-process locks are sufficient because the bot always runs a single uvicorn
worker (see the comment in MitupRuntime.run).
"""

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, override

from telegram import Update
from telegram.ext import BaseUpdateProcessor

LockKey = tuple[int | None, int | None]


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


class PerUserUpdateProcessor(BaseUpdateProcessor):
    __slots__ = ("_locks",)

    def __init__(self, max_concurrent_updates: int):
        super().__init__(max_concurrent_updates)
        self._locks: dict[LockKey, _KeyedLock] = {}

    @override
    async def do_process_update(self, update: object, coroutine: Awaitable[Any]):
        key = update_lock_key(update)
        if key is None:
            await coroutine
            return

        # The refcount mutations and dict lookups around each await run without an intervening
        # await point, so no other task can interleave with them: an entry is dropped exactly
        # when its last holder releases it, keeping the map bounded by in-flight updates.
        keyed_lock = self._locks.get(key)
        if keyed_lock is None:
            keyed_lock = _KeyedLock()
            self._locks[key] = keyed_lock
        keyed_lock.waiters += 1
        try:
            async with keyed_lock.lock:
                await coroutine
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
