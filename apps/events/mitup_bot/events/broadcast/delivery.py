"""Delivery phase: send each claimed body and classify its outcome, halting the batch on flood
control. See the package docstring in `__init__.py` for the retry and flood-control invariants."""

import datetime as dt

import structlog
from telegram.error import BadRequest, NetworkError, RetryAfter

from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.exceptions import InactiveUserInteraction
from mitup_bot.models import User
from mitup_bot.models.broadcasts import BroadcastDeliveryStatus
from mitup_bot.views import MitupView
from mitup_bot.views.factory import broadcast_recipient_view

from .types import (
    MAX_DELIVERY_ATTEMPTS,
    RETRY_AFTER_MARGIN_SECONDS,
    RETRY_BACKOFF_BASE_SECONDS,
    BatchResult,
    DeliveryClassification,
    DeliveryOutcome,
    PendingDelivery,
)

log = structlog.get_logger(__name__)


def build_recipient_views(bodies: dict[str, str]) -> dict[str, MitupView]:
    """Render each language's recipient view once, keyed by language, for the whole run.

    The delivered view depends only on `(body, language)`, so a broadcast to thousands of
    recipients would otherwise re-parse the HTML and re-run the gettext lookup once per recipient.
    Precomputing here collapses that to one build per language. Uses the shared
    `broadcast_recipient_view` so delivery stays identical to the operator preview.
    """
    return {language: broadcast_recipient_view(body_html, language) for language, body_html in bodies.items()}


async def deliver_batch(
    api: TelegramApiWrapper, broadcast_id: int, batch: list[PendingDelivery], views: dict[str, MitupView]
) -> BatchResult:
    """Deliver the batch in order, stopping the moment Telegram flood control fires. The
    triggering row's outcome is kept (a real API call happened, so its incremented attempt
    stands); the untried remainder is carried out for release, since hammering more sends into a
    known flood window would burn their capped attempts on non-attempts."""
    outcomes: list[DeliveryOutcome] = []
    for index, pending in enumerate(batch):
        classification = await deliver_one(api, pending.user, views[pending.language_sent], pending.attempt_count)
        outcome = resolve_delivery_outcome(pending, classification)
        log_delivery(broadcast_id, pending, outcome, classification)
        outcomes.append(outcome)
        if classification.flood_control:
            return BatchResult(
                outcomes=outcomes,
                flood_control=True,
                unattempted=batch[index + 1 :],
                flood_backoff=classification.retry_delay,
            )
    return BatchResult(outcomes=outcomes, flood_control=False)


async def deliver_one(
    api: TelegramApiWrapper, user: User, view: MitupView, attempt_count: int
) -> DeliveryClassification:
    """Send one prebuilt recipient view and classify the outcome.

    The view is the shared `broadcast_recipient_view` (see `build_recipient_views`), so preview and
    delivery are guaranteed to match. It is sent through `send_message_to_user`, which preserves the
    parsed entities and already classifies a blocked/deleted recipient as `InactiveUserInteraction`.
    Flood control (`RetryAfter`) and any unexpected error are transient and retryable (RETRY_PENDING
    with a backoff); a `BadRequest` is a permanent per-recipient failure; a `NetworkError` is
    systemic and re-raised to abort the run (a `TimedOut` may actually have delivered, so it can
    never be a retry — it stays orphan territory). None of these, except `NetworkError`, stops the
    fan-out.
    """
    try:
        await api.send_message_to_user(user, view)
    except InactiveUserInteraction:
        return DeliveryClassification(BroadcastDeliveryStatus.SKIPPED_INACTIVE, "bot blocked by user")
    except RetryAfter as error:
        delay = flood_control_backoff(error.retry_after)
        return DeliveryClassification(
            BroadcastDeliveryStatus.RETRY_PENDING, error.message, retry_delay=delay, flood_control=True
        )
    except BadRequest as error:
        return DeliveryClassification(BroadcastDeliveryStatus.FAILED, error.message)
    except NetworkError:
        raise
    except Exception as error:
        delay = dt.timedelta(seconds=RETRY_BACKOFF_BASE_SECONDS * 2 ** (attempt_count - 1))
        return DeliveryClassification(BroadcastDeliveryStatus.RETRY_PENDING, str(error), retry_delay=delay)
    return DeliveryClassification(BroadcastDeliveryStatus.SENT, None)


def flood_control_backoff(retry_after: int | dt.timedelta) -> dt.timedelta:
    """Telegram's requested flood-control wait plus a margin so the retry lands past the window.
    `RetryAfter.retry_after` is seconds or a timedelta depending on PTB's configuration."""
    window = retry_after if isinstance(retry_after, dt.timedelta) else dt.timedelta(seconds=retry_after)
    return window + dt.timedelta(seconds=RETRY_AFTER_MARGIN_SECONDS)


def resolve_delivery_outcome(pending: PendingDelivery, classification: DeliveryClassification) -> DeliveryOutcome:
    """Apply the per-delivery attempt cap: a transient failure at or past `MAX_DELIVERY_ATTEMPTS`
    becomes a permanent FAILED, otherwise it is scheduled for retry after its backoff."""
    if classification.status is not BroadcastDeliveryStatus.RETRY_PENDING:
        return DeliveryOutcome(pending.delivery_id, pending.user.db_id, classification.status)
    if pending.attempt_count >= MAX_DELIVERY_ATTEMPTS:
        return DeliveryOutcome(pending.delivery_id, pending.user.db_id, BroadcastDeliveryStatus.FAILED)
    assert classification.retry_delay is not None, "A RETRY_PENDING classification always carries a backoff"
    next_attempt_time = dt.datetime.now(dt.UTC) + classification.retry_delay
    return DeliveryOutcome(
        pending.delivery_id, pending.user.db_id, BroadcastDeliveryStatus.RETRY_PENDING, next_attempt_time
    )


def log_delivery(
    broadcast_id: int, pending: PendingDelivery, outcome: DeliveryOutcome, classification: DeliveryClassification
):
    emit = log.info if outcome.status is BroadcastDeliveryStatus.SENT else log.warning
    retry_in = None
    if outcome.status is BroadcastDeliveryStatus.RETRY_PENDING and classification.retry_delay is not None:
        retry_in = round(classification.retry_delay.total_seconds())
    emit(
        "broadcast_delivery",
        broadcast_id=broadcast_id,
        tg_user_id=pending.user.tg_user_id,
        lang=pending.language_sent,
        outcome=outcome.status.value,
        attempt=pending.attempt_count,
        retry_in=retry_in,
        error=classification.error,
    )
