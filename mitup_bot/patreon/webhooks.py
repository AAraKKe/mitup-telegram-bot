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

The single-row :class:`~mitup_bot.models.PatreonWebhook` I/O (the stored id/uri/secret) lives here too,
so both the registration writer and the endpoint's signature-secret reader share one module rather
than each re-selecting the table.
"""

import structlog
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import db, patreon
from mitup_bot.models import PatreonWebhook
from mitup_bot.monitoring.client import MetricsClient
from mitup_bot.monitoring.metric_keys import MetricKey
from mitup_bot.patreon.client import MEMBER_WEBHOOK_TRIGGERS, PatreonClient
from mitup_bot.patreon.creator_token import acquire_creator_access_token
from mitup_bot.patreon.models import WebhookResource

log = structlog.get_logger(__name__)

# Stable machine key bound on every log line of a single registration attempt.
REGISTRATION_FLOW = "patreon_webhook_registration"

# The path the membership webhook is served on (mirrors the ``/telegram`` path).
MEMBER_WEBHOOK_PATH = "/patreon/webhook"
# Patreon serializes the registered URI's query string into every delivery, so these params are what
# make each payload carry the ``user`` relationship and ``patron_status``/entitled amount; v2 omits
# unrequested attributes, so without them the delivered member would be a bare id/type.
MEMBER_PAYLOAD_QUERY = "include=user&fields[member]=patron_status,currently_entitled_amount_cents"


def webhook_uri(domain: str, port: int) -> str:
    """The public URI Patreon should POST membership events to, mirroring the Telegram webhook URL."""
    return f"https://{domain}:{port}{MEMBER_WEBHOOK_PATH}?{MEMBER_PAYLOAD_QUERY}"


@db.with_session
async def load_webhook_secret(session: AsyncSession) -> str | None:
    """The HMAC secret of the registered webhook (decrypted by the column), or ``None`` when no webhook
    has been registered yet. Read by the endpoint to verify delivery signatures."""
    row = (await session.exec(select(PatreonWebhook))).first()
    return row.secret if row is not None else None


@db.with_session
async def store_webhook(session: AsyncSession, *, patreon_webhook_id: str, uri: str, secret: str):
    """Upsert the single ``patreon_webhooks`` row with the current id/uri/secret (Fernet-encrypted)."""
    row = (await session.exec(select(PatreonWebhook))).first()
    if row is None:
        session.add(PatreonWebhook(patreon_webhook_id=patreon_webhook_id, uri=uri, secret=secret))
        return
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
    if not patreon.is_configured():
        # Defensive: app.py only passes a URL when Patreon is configured, but keep the no-op local so a
        # direct caller never trips current_config()'s raise into a spurious failure metric.
        log.info("Patreon not configured, skipping webhook registration")
        return

    with structlog.contextvars.bound_contextvars(flow=REGISTRATION_FLOW):
        try:
            config = patreon.current_config()
            async with PatreonClient(config) as client:
                access_token = await acquire_creator_access_token(client, config)
                if access_token is None:
                    log.error("Skipping Patreon webhook registration, no creator token available")
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
    existing = await client.list_webhooks(access_token)
    match = next((webhook for webhook in existing if uri_matches(webhook.attributes.uri, desired_uri)), None)

    if match is None:
        created = await client.create_webhook(access_token, uri=desired_uri, triggers=MEMBER_WEBHOOK_TRIGGERS)
        await persist(created, desired_uri)
        log.info("Registered new Patreon membership webhook", patreon_webhook_id=created.id, uri=desired_uri)
        return

    if drifted(match, desired_uri):
        await client.update_webhook(access_token, match.id, uri=desired_uri, triggers=MEMBER_WEBHOOK_TRIGGERS)
        log.info("Re-pointed existing Patreon membership webhook", patreon_webhook_id=match.id, uri=desired_uri)
    await persist(match, desired_uri)


def drifted(webhook: WebhookResource, desired_uri: str) -> bool:
    """Whether the existing webhook's uri or trigger set differs from what we want registered."""
    triggers_changed = sorted(webhook.attributes.triggers) != sorted(MEMBER_WEBHOOK_TRIGGERS)
    return webhook.attributes.uri != desired_uri or triggers_changed


async def persist(webhook: WebhookResource, desired_uri: str):
    """Persist the webhook's id, our canonical uri, and its secret; skip when the secret is absent.

    The secret comes from a create response or a ``fields[webhook]=...secret...`` list read; if it is
    somehow missing we do not overwrite the stored row with a null, which would break verification."""
    if webhook.secret is None:
        log.warning("Patreon webhook carried no secret, leaving stored secret unchanged", patreon_webhook_id=webhook.id)
        return
    await store_webhook(patreon_webhook_id=webhook.id, uri=desired_uri, secret=webhook.secret)
