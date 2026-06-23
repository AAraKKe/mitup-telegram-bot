import json
import secrets
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from telegram import Update
from telegram.ext import Application

from mitup_bot.monitoring.client import MetricsClient
from mitup_bot.monitoring.metric_keys import MetricKey

log = structlog.get_logger(__name__)

TELEGRAM_SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"

router = APIRouter()


def get_ptb_application(request: Request) -> Application:
    return request.app.state.ptb_app


def get_webhook_secret(request: Request) -> str | None:
    return request.app.state.secret_token


def get_metrics_client(request: Request) -> MetricsClient:
    return request.app.state.metrics_client


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
    if expected_secret is None or received is None or not secrets.compare_digest(received, expected_secret):
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
) -> None:
    # Telegram only reads the HTTP status code; it ignores the response body.
    # We always return 204 (even on parse failures or handler exceptions) so
    # Telegram doesn't retry poison-pill updates.
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

    try:
        await ptb_app.process_update(update)
    except Exception:
        log.exception("Unhandled exception while processing Telegram update")
