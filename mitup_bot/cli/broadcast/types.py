"""Shared value objects and tuning constants for the broadcast sender. See the package
docstring in `__init__.py` for the full send-mechanism and durability invariants."""

import datetime as dt
from dataclasses import dataclass, field

from mitup_bot.models import User
from mitup_bot.models.broadcasts import BroadcastDeliveryStatus, BroadcastStatus
from mitup_bot.translations import TranslationEngine

# Once this many runs have each claimed the broadcast without completing it, declare it FAILED.
MAX_BROADCAST_ATTEMPTS = 5
# Recipients handled per PENDING-query page; also the crash re-send window under immediate mode.
BROADCAST_BATCH_SIZE = 50
# Times a single delivery is sent before a transient failure at this attempt is failed permanently.
MAX_DELIVERY_ATTEMPTS = 3
# Base backoff for an unexpected send error; doubles with each attempt.
RETRY_BACKOFF_BASE_SECONDS = 60
# Added to Telegram's requested flood-control wait so the retry lands past the window's edge.
RETRY_AFTER_MARGIN_SECONDS = 5
# The anonymous-invitee sentinel is never a reachable recipient.
ANONYMOUS_INVITEE_TG_ID = -1
FALLBACK_LANG = TranslationEngine.FALLBACK_LANG


@dataclass
class ClaimedBroadcast:
    broadcast_id: int
    attempts: int
    terminal_failure: bool


@dataclass
class PendingDelivery:
    delivery_id: int
    user: User
    language_sent: str
    attempt_count: int


@dataclass
class DeliveryClassification:
    """How `deliver_one` classified a single send, before the per-delivery attempt cap is applied.

    A RETRY_PENDING status means the send failed transiently and was not delivered; `retry_delay`
    carries the backoff and `flood_control` marks a Telegram `RetryAfter` (which halts the run).
    """

    status: BroadcastDeliveryStatus
    error: str | None
    retry_delay: dt.timedelta | None = None
    flood_control: bool = False


@dataclass
class DeliveryOutcome:
    delivery_id: int
    user_id: int
    status: BroadcastDeliveryStatus
    next_attempt_time: dt.datetime | None = None


@dataclass
class BatchResult:
    """The resolved outcomes of one delivered batch. `flood_control` tells `send_all_pending` to
    stop claiming further batches this run. When flood control halts the batch mid-way,
    `unattempted` carries the still-IN_PROGRESS rows the loop never sent, to be released back to
    RETRY_PENDING after `flood_backoff` with their claim increment undone."""

    outcomes: list[DeliveryOutcome]
    flood_control: bool
    unattempted: list[PendingDelivery] = field(default_factory=list)
    flood_backoff: dt.timedelta | None = None


@dataclass
class LanguageBreakdown:
    language: str
    sent: int
    failed: int
    skipped: int
    orphaned: int = 0


@dataclass
class BroadcastSummary:
    name: str
    status: BroadcastStatus
    attempts: int
    total: int
    sent: int
    failed: int
    skipped: int
    breakdown: list[LanguageBreakdown]
    orphaned: int = 0
