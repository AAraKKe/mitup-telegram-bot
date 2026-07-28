import json
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from telegram import Update
from telegram.ext import Application

from mitup_bot.monitoring.client import MetricsClient
from mitup_bot.monitoring.metric_keys import MetricKey
from mitup_bot.update_trace import update_log_context
from mitup_bot.web.dependencies import get_metrics_client, get_ptb_application, get_webhook_secret
from mitup_bot.web.utils import secret_header_matches

log = structlog.get_logger(__name__)

TELEGRAM_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"

router = APIRouter()


def validate_secret(
    request: Request,
    expected_secret: str | None,
    metrics_client: MetricsClient,
):
    """Validate the Telegram secret header using a constant-time comparison.

    Raises HTTPException(403) when the header is missing or does not match the
    configured secret. Emits a metric and logs at WARNING with the caller's host
    but never the received token.
    """
    received = request.headers.get(TELEGRAM_SECRET_HEADER)
    if not secret_header_matches(received, expected_secret):
        metrics_client.emit(MetricKey.WEBHOOK_FORBIDDEN)
        client_host = request.client.host if request.client is not None else "unknown"
        log.warning("Rejected webhook request, invalid or missing secret header", client_host=client_host)
        raise HTTPException(status_code=403)


def parse_update(payload: dict[str, Any], ptb_app: Application, metrics_client: MetricsClient) -> Update | None:
    """Parse the raw JSON payload into a Telegram Update.

    Returns None when the payload cannot be parsed. The caller is expected to
    respond with 2xx to prevent Telegram from retrying poison-pill updates.
    """
    try:
        return Update.de_json(payload, bot=ptb_app.bot)
    except ValueError, KeyError, TypeError:
        metrics_client.emit(MetricKey.WEBHOOK_MALFORMED_UPDATE)
        log.exception("Failed to parse Telegram update")
        return None


@router.post("/telegram", status_code=204)
async def telegram_webhook(
    request: Request,
    ptb_app: Annotated[Application, Depends(get_ptb_application)],
    secret_token: Annotated[str | None, Depends(get_webhook_secret)],
    metrics_client: Annotated[MetricsClient, Depends(get_metrics_client)],
):
    # Telegram only reads the HTTP status code; it ignores the response body.
    # We always return 204 (even on parse failures) so Telegram doesn't retry
    # poison-pill updates. Well-formed updates are handed to PTB's update queue
    # and processed out of band, so the request returns before processing runs.
    validate_secret(request, secret_token, metrics_client)

    body = await request.body()
    try:
        payload = json.loads(body)
    except ValueError, TypeError:
        metrics_client.emit(MetricKey.WEBHOOK_MALFORMED_UPDATE)
        log.exception("Failed to decode webhook JSON body", bytes=len(body))
        return

    update = parse_update(payload, ptb_app, metrics_client)
    if update is None:
        return

    # The arrival timestamp, to difference against the processing that starts out of band, and the
    # depth of the queue it is being handed to — a backlog is otherwise only visible as latency.
    chat = update.effective_chat
    log.info(
        "Telegram update received",
        **update_log_context(update),
        chat_type=chat.type if chat is not None else None,
        queue_depth=ptb_app.update_queue.qsize(),
        transport="webhook",
    )

    # Enqueue for PTB's update processor rather than calling process_update()
    # directly. The fetcher task started by Application.start() drains this queue
    # and applies the concurrent_updates semaphore; a direct call bypasses that
    # (unbounded concurrency) and holds the request open for the full processing
    # time, risking Telegram timeout re-delivery. The queue is unbounded, so this
    # returns immediately; processing failures surface via PTB's error handler.
    try:
        await ptb_app.update_queue.put(update)
    except Exception:
        # Enqueuing an unbounded queue shouldn't raise, but a broken event loop or
        # app state could. Swallow it so we still return 204 — a non-2xx would make
        # Telegram retry the update in a tight loop.
        log.exception("Failed to enqueue Telegram update")
