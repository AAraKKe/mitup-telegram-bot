"""The rate limiter every bot process installs on its PTB application."""

import asyncio
import contextlib
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import structlog
from telegram import constants
from telegram.error import RetryAfter
from telegram.ext import AIORateLimiter

from mitup_bot.monitoring import MetricKey, MetricUnit
from mitup_bot.monitoring.client import current_metrics_client

log = structlog.get_logger(__name__)

ApiResponse = bool | dict[str, Any] | list[dict[str, Any]]

THROTTLED_LOG_EVENT = "Telegram rate limit hit"
# Telegram's retry_after is the second the bot may retry *in*, so waiting exactly that long can
# land back inside the same window. PTB pads by the same margin.
RETRY_AFTER_GRACE_SECONDS = 0.1


class RequestScope(StrEnum):
    """Which flood limit a request is shaped by, and the key its retry-after pause is held under."""

    CHAT = "chat"
    GROUP = "group"
    INLINE_MESSAGE = "inline_message"
    UNKEYED = "unkeyed"


PauseKey = tuple[RequestScope, int | str | None]


@dataclass(frozen=True)
class RequestKey:
    scope: RequestScope
    chat_id: int | str | None

    @property
    def counts_against_overall(self) -> bool:
        """Whether the bot-wide 30/s budget applies to this request.

        An inline-addressed edit still delivers a message to a chat, so it spends that budget like
        any ordinary send. Only a request that reaches no recipient stays outside the bucket.
        """
        return self.scope is not RequestScope.UNKEYED

    @property
    def group(self) -> int | str | bool:
        """The per-group bucket's key, or False for a request no group limit applies to."""
        if self.scope is not RequestScope.GROUP or self.chat_id is None:
            return False
        return self.chat_id

    @property
    def pause_key(self) -> PauseKey | None:
        """The pause this request waits on, or None when it belongs to no key in particular."""
        if self.scope is RequestScope.UNKEYED:
            return None
        return (self.scope, self.chat_id)


def request_key(data: dict[str, Any]) -> RequestKey:
    """Name the flood-limit scope a request is addressed under.

    A request identified only by `inline_message_id` reaches a chat the bot cannot see, so it
    shares one key with every other inline edit instead of resolving to a chat of its own.
    """
    chat_id = data.get("chat_id")
    if chat_id is None:
        inline = data.get("inline_message_id") is not None
        return RequestKey(RequestScope.INLINE_MESSAGE if inline else RequestScope.UNKEYED, None)

    # A chat id may arrive as a string holding an integer, so coerce before testing the sign.
    with contextlib.suppress(ValueError, TypeError):
        chat_id = int(chat_id)

    # `data` holds the outgoing request parameters and nothing else, so the id itself is all there
    # is to go on: Telegram numbers users positive and groups, supergroups and channels negative,
    # and what survives the coercion above as a string is a @username, which only supergroups and
    # channels have. This is the test PTB's own AIORateLimiter applies. Reading `Chat.type` would
    # mean a getChat round trip per request, which a rate limiter cannot make.
    addresses_group = (isinstance(chat_id, int) and chat_id < 0) or isinstance(chat_id, str)
    return RequestKey(RequestScope.GROUP if addresses_group else RequestScope.CHAT, chat_id)


def log_rate_limit_hit(api_method: str, scope: RequestScope, retry_after_s: float, *, exhausted: bool):
    """Report one 429 on the log plane, before anything sleeps on it.

    Only a 429 earns a line. Shaping that merely made a request wait its turn is the limiter doing
    its job and rides the metric alone, so a line here always means Telegram refused the call.
    """
    log.warning(
        THROTTLED_LOG_EVENT,
        api_method=api_method,
        scope=scope.value,
        retry_after_s=retry_after_s,
        outcome="exhausted" if exhausted else "retrying",
    )


def record_throttled(*, delayed: bool):
    """Report whether one request was held up before it executed.

    The sample rides the ambient client, so it joins the flush window of whichever invocation
    provoked it and inherits that window's correlation key. Outside an invocation there is no
    window to join and no client of ours that anything would flush, so the sample is dropped.
    """
    if client := current_metrics_client():
        client.emit_aggregate(MetricKey.TELEGRAM_THROTTLED, float(delayed), MetricUnit.COUNT)


class RetryAfterPauses:
    """The deadlines that hold back requests Telegram has already throttled.

    A 429 names no scope. The Bot API server answers with the same `retry_after` shape whether one
    chat, the bot, or Telegram's own backend produced it, so a pause is attributed to the key the
    throttled request was shaped by and to nothing else. That direction is the safe one. Blaming a
    single key for a bot-wide limit lets the other keys bleed one request each before they pause
    too, bounded by the overall bucket. Blaming the whole process for a per-chat limit stops every
    unrelated chat for a `retry_after` the server hardcodes to 60 seconds. A request that belongs
    to no key pauses everything, being the only conservative answer left.
    """

    def __init__(self):
        self.deadlines: dict[PauseKey, float] = {}
        self.global_deadline: float = 0.0

    def remaining(self, key: PauseKey | None) -> float:
        """Seconds left before *key* may fire, counting any global pause over it."""
        keyed = self.deadlines.get(key, 0.0) if key is not None else 0.0
        return max(keyed, self.global_deadline) - time.monotonic()

    def hold(self, key: PauseKey | None, seconds: float):
        now = time.monotonic()
        self.prune(now)
        deadline = now + seconds
        if key is None:
            self.global_deadline = max(self.global_deadline, deadline)
            return
        self.deadlines[key] = max(self.deadlines.get(key, 0.0), deadline)

    def prune(self, now: float):
        """Drop elapsed pauses, so the map holds only keys currently being throttled."""
        for key in [key for key, deadline in self.deadlines.items() if deadline <= now]:
            del self.deadlines[key]

    async def wait(self, key: PauseKey | None):
        """Sleep until *key* is clear, re-reading the deadline in case it moved out while waiting."""
        while (remaining := self.remaining(key)) > 0:
            await asyncio.sleep(remaining)


class MitupRateLimiter(AIORateLimiter):
    """`AIORateLimiter` with inline-addressed requests shaped and retry-after pauses attributed.

    Two departures from the base class, both of them in `process_request`:

    * A request carrying only an `inline_message_id` counts against the overall bucket. The base
      class gates that bucket on `chat_id` alone, which leaves edits to inline-shared cards, most
      of this bot's outbound volume, shaped by nothing at all.
    * A `RetryAfter` holds back the key it was raised for instead of every request in the process.
      `RetryAfterPauses` carries the reasoning.

    The buckets themselves are the base class's, reached through `_run_request`, and so are its
    limiter cache and its constructor knobs. The pause is applied before that call rather than
    inside it, so a waiting request does not first spend the bucket capacity it is about to sit on.
    """

    __slots__ = ("pauses",)

    def __init__(
        self,
        overall_max_rate: float = constants.FloodLimit.MESSAGES_PER_SECOND,
        overall_time_period: float = 1,
        group_max_rate: float = constants.FloodLimit.MESSAGES_PER_MINUTE_PER_GROUP,
        group_time_period: float = 60,
        max_retries: int = 0,
    ):
        super().__init__(
            overall_max_rate=overall_max_rate,
            overall_time_period=overall_time_period,
            group_max_rate=group_max_rate,
            group_time_period=group_time_period,
            max_retries=max_retries,
        )
        self.pauses = RetryAfterPauses()

    def buckets_depleted(self, key: RequestKey, *, allow_paid_broadcast: bool) -> bool:
        """Whether a bucket this request must pass through is out of capacity right now.

        Read before acquiring, mirroring the gating `_run_request` applies. Another task can take
        the last slot between this read and the acquisition, so a sample can be labelled wrongly
        either way. The race costs a rounding error on a telemetry series and nothing else.
        """
        if allow_paid_broadcast:
            return not self._apb_limiter.has_capacity()
        if key.counts_against_overall and self._base_limiter and not self._base_limiter.has_capacity():
            return True
        if not (key.group and self._group_max_rate):
            return False
        return not self._get_group_limiter(key.group).has_capacity()

    def on_throttled(self, key: RequestKey, endpoint: str, exc: RetryAfter, *, exhausted: bool):
        """Log the 429 and hold the pause its key must now wait out.

        The pause is held whether or not this request retries. The wait Telegram asked for binds
        the key, not the call, and `max_retries` defaults to 0, so holding it only on the way to a
        retry would leave the registry empty for every limiter built with that default.
        """
        # The public `retry_after` is mid-deprecation between int and timedelta; the private
        # timedelta is what PTB reads internally and is stable in both shapes.
        retry_after_s = exc._retry_after.total_seconds()  # noqa: SLF001
        log_rate_limit_hit(endpoint, key.scope, retry_after_s, exhausted=exhausted)
        self.pauses.hold(key.pause_key, retry_after_s + RETRY_AFTER_GRACE_SECONDS)

    async def process_request(
        self,
        callback: Callable[..., Coroutine[Any, Any, ApiResponse]],
        args: Any,
        kwargs: dict[str, Any],
        endpoint: str,
        data: dict[str, Any],
        rate_limit_args: int | None,
    ) -> ApiResponse:
        key = request_key(data)
        allow_paid_broadcast = data.get("allow_paid_broadcast", False)
        max_retries = rate_limit_args or self._max_retries
        attempt = 0
        delayed = False

        try:
            while True:
                delayed = delayed or self.pauses.remaining(key.pause_key) > 0
                await self.pauses.wait(key.pause_key)
                delayed = delayed or self.buckets_depleted(key, allow_paid_broadcast=allow_paid_broadcast)
                try:
                    return await self._run_request(
                        chat=key.counts_against_overall,
                        group=key.group,
                        allow_paid_broadcast=allow_paid_broadcast,
                        callback=callback,
                        args=args,
                        kwargs=kwargs,
                    )
                except RetryAfter as exc:
                    delayed = True
                    exhausted = attempt == max_retries
                    self.on_throttled(key, endpoint, exc, exhausted=exhausted)
                    if exhausted:
                        raise
                    attempt += 1
        finally:
            record_throttled(delayed=delayed)
