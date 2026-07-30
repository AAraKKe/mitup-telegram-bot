"""Idempotent startup registration of the Patreon membership webhook, plus the row I/O behind it.

This is the real-time analog of Telegram's ``bot.set_webhook``: on boot (webhook mode, a public
domain, and Patreon configured) we make sure Patreon is delivering ``members:*`` events to our
``/patreon/webhook`` endpoint and persist the delivery ``secret`` so the endpoint can verify each
signature. Two properties distinguish it from ``set_webhook``:

- **Idempotent** — the webhook is matched by URI, so a redeploy never creates a duplicate; a drifted
  URI or trigger set is PATCHed in place.
- **Failure-isolated** — Patreon is optional and the daily reconciliation job is the backstop, so any
  registration error is logged and metered but never aborts bot startup. ``set_webhook`` is allowed to
  hard-fail; this is not.

The single-row :class:`~mitup_bot.models.PatreonWebhook` I/O (the stored id/uri/secret) lives here too.
"""

from enum import StrEnum

import structlog
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import db, patreon
from mitup_bot.models import PatreonWebhook
from mitup_bot.monitoring.client import MetricsClient, bound_metrics_client
from mitup_bot.monitoring.metric_keys import MetricKey
from mitup_bot.patreon.client import MEMBER_WEBHOOK_TRIGGERS, PatreonClient
from mitup_bot.patreon.creator_token import acquire_creator_access_token
from mitup_bot.patreon.models import WebhookResource

log = structlog.get_logger(__name__)

# Stable machine key bound on every log line of a single registration attempt.
REGISTRATION_FLOW = "patreon_webhook_registration"

# The path the membership webhook is served on.
MEMBER_WEBHOOK_PATH = "/patreon/webhook"
# Patreon serializes the registered URI's query string into every delivery, so these params are what
# make each payload carry the ``user`` relationship and ``patron_status``/entitled amount; v2 omits
# unrequested attributes, so without them the delivered member would be a bare id/type.
MEMBER_PAYLOAD_QUERY = "include=user&fields[member]=patron_status,currently_entitled_amount_cents"


class WebhookDrift(StrEnum):
    """Which part of an existing webhook no longer matches what we want registered."""

    URI_CHANGED = "uri_changed"
    TRIGGERS_CHANGED = "triggers_changed"
    URI_AND_TRIGGERS_CHANGED = "uri_and_triggers_changed"


def webhook_uri(domain: str, port: int) -> str:
    """The public URI Patreon should POST membership events to."""
    return f"https://{domain}:{port}{MEMBER_WEBHOOK_PATH}?{MEMBER_PAYLOAD_QUERY}"


@db.with_session
async def load_webhook_secret(session: AsyncSession) -> str | None:
    """The HMAC secret of the registered webhook (decrypted by the column), or ``None`` when no webhook
    has been registered yet. Read by the endpoint to verify delivery signatures."""
    row = (await session.exec(select(PatreonWebhook))).first()
    if row is None:
        log.warning("No Patreon webhook secret stored, deliveries cannot be verified", reason="no_webhook_row")
        return None
    return row.secret


@db.with_session
async def store_webhook(session: AsyncSession, *, patreon_webhook_id: str, uri: str, secret: str):
    """Upsert the single ``patreon_webhooks`` row with the current id/uri/secret (Fernet-encrypted)."""
    row = (await session.exec(select(PatreonWebhook))).first()
    if row is None:
        session.add(PatreonWebhook(patreon_webhook_id=patreon_webhook_id, uri=uri, secret=secret))
        log.info(
            "Stored Patreon webhook registration",
            action="insert",
            patreon_webhook_id=patreon_webhook_id,
            uri=uri,
            secret_rotated=True,
        )
        return
    log.info(
        "Stored Patreon webhook registration",
        action="update",
        patreon_webhook_id=patreon_webhook_id,
        uri=uri,
        # Whether verification of in-flight deliveries changes key with this write.
        secret_rotated=row.secret != secret,
    )
    row.patreon_webhook_id = patreon_webhook_id
    row.uri = uri
    row.secret = secret


def uri_matches(existing_uri: str | None, desired_uri: str) -> bool:
    """Whether an existing webhook is *ours*, matched on the base URI (scheme+host+path) so a query-param
    or encoding difference in what Patreon echoes back never reads as a different endpoint."""
    return existing_uri is not None and existing_uri.split("?", 1)[0] == desired_uri.split("?", 1)[0]


async def register_membership_webhook(desired_uri: str, metrics_client: MetricsClient):
    """Ensure Patreon delivers ``members:*`` events to ``desired_uri`` and persist the signing secret.

    Wholly failure-isolated: any error (no creator token, Patreon API failure, DB error) is logged and
    metered but swallowed, because a registration failure must never abort bot startup. Called from the
    webhook lifespan after ``set_webhook``, only when Patreon is configured and a public domain exists.
    """
    # `stage` is bound rather than passed so the catch-all below names the step that raised. Advancing
    # it with bind_contextvars is safe: the enclosing bound_contextvars resets the key on exit.
    # Startup runs outside any invocation, so without an ambient client the Patreon round-trips below
    # would produce lines with no timing samples behind them.
    with (
        structlog.contextvars.bound_contextvars(flow=REGISTRATION_FLOW, stage="acquire_token"),
        bound_metrics_client(metrics_client),
    ):
        log.info("Ensuring Patreon membership webhook", desired_uri=desired_uri)
        try:
            config = patreon.current_config()
            async with PatreonClient(config) as client:
                access_token = await acquire_creator_access_token(client, config)
                if access_token is None:
                    log.error(
                        "Skipping Patreon webhook registration, no creator token available",
                        reason="no_creator_token",
                    )
                    metrics_client.emit(MetricKey.PATREON_WEBHOOK_REGISTRATION_FAILED)
                    return
                await ensure_webhook(client, access_token, desired_uri)
        except Exception:
            metrics_client.emit(MetricKey.PATREON_WEBHOOK_REGISTRATION_FAILED)
            log.exception("Patreon webhook registration failed")


async def ensure_webhook(client: PatreonClient, access_token: str, desired_uri: str):
    """Create the webhook if absent, or PATCH it when its uri/triggers drifted, then persist the secret.

    ``list_webhooks`` requests ``secret`` explicitly, so the listed match already carries the signing key
    we persist — the PATCH response is not relied on for it."""
    structlog.contextvars.bind_contextvars(stage="list")
    existing = await client.list_webhooks(access_token)
    match = next((webhook for webhook in existing if uri_matches(webhook.attributes.uri, desired_uri)), None)
    log.info("Inspected Patreon webhooks", count=len(existing), matched=match is not None, desired_uri=desired_uri)

    if match is None:
        structlog.contextvars.bind_contextvars(stage="create")
        created = await client.create_webhook(access_token, uri=desired_uri, triggers=MEMBER_WEBHOOK_TRIGGERS)
        await persist(created, desired_uri)
        log.info("Registered new Patreon membership webhook", patreon_webhook_id=created.id, uri=desired_uri)
        return

    report_delivery_health(match)
    drift = drift_reason(match, desired_uri)
    if drift is not None:
        structlog.contextvars.bind_contextvars(stage="update")
        await client.update_webhook(access_token, match.id, uri=desired_uri, triggers=MEMBER_WEBHOOK_TRIGGERS)
        log.info(
            "Re-pointed existing Patreon membership webhook",
            patreon_webhook_id=match.id,
            uri=desired_uri,
            reason=str(drift),
            previous_uri=match.attributes.uri,
            previous_triggers=sorted(match.attributes.triggers),
        )
    else:
        log.info("Patreon membership webhook already up to date", patreon_webhook_id=match.id, reason="no_drift")
    await persist(match, desired_uri)


def report_delivery_health(webhook: WebhookResource):
    """Surface the two attributes that say Patreon has stopped delivering to us.

    Patreon pauses a webhook it cannot deliver to and sends nothing to say so: no event arrives and
    the daily reconciliation silently becomes the only mechanism. This read is the one place in the
    process where that fact is available.
    """
    if not webhook.attributes.paused and webhook.attributes.num_consecutive_times_failed == 0:
        return
    log.warning(
        "Patreon webhook is paused or failing",
        patreon_webhook_id=webhook.id,
        paused=webhook.attributes.paused,
        consecutive_failures=webhook.attributes.num_consecutive_times_failed,
        last_attempted_at=webhook.attributes.last_attempted_at,
    )


def drift_reason(webhook: WebhookResource, desired_uri: str) -> WebhookDrift | None:
    """Which part of the existing webhook differs from what we want registered, or None when neither
    does. The caller needs the distinction, not just the fact: the PATCH line names what it corrected."""
    uri_changed = webhook.attributes.uri != desired_uri
    triggers_changed = sorted(webhook.attributes.triggers) != sorted(MEMBER_WEBHOOK_TRIGGERS)
    match (uri_changed, triggers_changed):
        case (True, True):
            return WebhookDrift.URI_AND_TRIGGERS_CHANGED
        case (True, False):
            return WebhookDrift.URI_CHANGED
        case (False, True):
            return WebhookDrift.TRIGGERS_CHANGED
        case _:
            return None


async def persist(webhook: WebhookResource, desired_uri: str):
    """Persist the webhook's id, our canonical uri, and its secret; skip when the secret is absent.

    The secret comes from a create response or a ``fields[webhook]=...secret...`` list read; if it is
    somehow missing we do not overwrite the stored row with a null, which would break verification."""
    structlog.contextvars.bind_contextvars(stage="persist")
    if webhook.secret is None:
        log.warning(
            "Patreon webhook carried no secret, leaving stored secret unchanged",
            patreon_webhook_id=webhook.id,
            reason="secret_absent_in_response",
        )
        return
    await store_webhook(patreon_webhook_id=webhook.id, uri=desired_uri, secret=webhook.secret)
