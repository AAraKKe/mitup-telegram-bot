"""The Patreon OAuth callback endpoint and its browser result pages.

Patreon redirects the user's browser here after the consent screen, so unlike ``/telegram`` this
route renders HTML for a human and deep-links back to the bot chat. It owns its own DB and api
plumbing: there is no per-request session dependency, so it opens its own transaction and builds an
api from the PTB bot for the webhook route.

The callback grants nothing. A browser can prove which Patreon account consented but not whose
Telegram account is behind it, and the consent URL is transferable, so the endpoint parks the proven
identity as a pending link and renders a pairing code. The link is completed in the bot, against the
account that sends the code (see :mod:`mitup_bot.patreon_link`).

The page markup lives in ``templates/patreon_result.html`` (a Mitup-branded shell filled via
``string.Template``); only the per-outcome title and message live here. This copy is intentionally
plain, hardcoded English rather than going through the message/translation pipeline: the pages render
in a browser with no ``lang`` context (the failure pages fire before we can even resolve the user),
and there is no gettext catalog for HTML. The Telegram messages the user receives back in the chat
*do* go through the translated pipeline. Every failure render is logged with enough context to trace
a support question back to its cause.
"""

import base64
import datetime as dt
import hashlib
import hmac
import html
import uuid
from dataclasses import dataclass
from enum import Enum, StrEnum, auto
from pathlib import Path
from string import Template
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlmodel import select
from telegram.ext import Application

from mitup_bot import db, patreon, supporter
from mitup_bot.api_wrapper import BotAdapter, TelegramApiWrapper, build_api
from mitup_bot.config import PatreonConfig
from mitup_bot.exceptions import PatreonApiError, PatreonStateExpired, PatreonStateInvalid, PatreonTokenRevoked
from mitup_bot.models import SupporterSubscription, User
from mitup_bot.monitoring.client import MetricsClient
from mitup_bot.monitoring.metric_keys import Feature, MetricKey
from mitup_bot.patreon import PatreonClient, oauth, pairing, webhooks
from mitup_bot.patreon.client import MEMBER_DELETE_TRIGGER
from mitup_bot.patreon.models import MemberResource, WebhookMemberPayload
from mitup_bot.patreon.pending_links import stage_pending_link
from mitup_bot.patreon_link import SUPPORT_GRACE_DAYS, HostsGroupTrigger, readmit_to_hosts_group
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils.messages import SupporterNotificationMessages
from mitup_bot.web.dependencies import get_metrics_client, get_ptb_application
from mitup_bot.web.utils import secret_header_matches

log = structlog.get_logger(__name__)

# Stable machine key bound on every log line of a single callback invocation, so the whole OAuth
# round-trip can be filtered as one series in CloudWatch Logs Insights.
OAUTH_FLOW = "patreon_oauth_callback"

router = APIRouter()

RESULT_TEMPLATE = Template((Path(__file__).parent / "templates" / "patreon_result.html").read_text(encoding="utf-8"))

# The corner branding is embedded as a base64 data URI rather than an <img src> URL: these pages are
# served standalone with no static-file mount, so an external reference would 404. Encoding the
# transparent horizontal lockup once at import keeps every rendered page self-contained and the logo
# pixel-accurate (no dependency on the viewer's fonts). The asset ships in the wheel like the template.
LOGO_BYTES = (Path(__file__).parent / "assets" / "logo-horizontal-transparent.png").read_bytes()
LOGO_IMG = f'<img class="logo" src="data:image/png;base64,{base64.b64encode(LOGO_BYTES).decode("ascii")}" alt="Mitup">'

# Reused across the failure pages: every one points the user back to the same in-bot button.
RETRY_HINT = "Head back to Mitup and tap Link Patreon account in the Collaborate menu"


class PatreonCallbackParams(BaseModel):
    """The query parameters Patreon may send back on the redirect.

    Everything is optional and extras are ignored on purpose: a strict model would raise, and any
    validation error escapes FastAPI as a 422 JSON response that bypasses our branded HTML pages.
    Scanners and Patreon appending unknown query params must classify normally, not 422.
    """

    model_config = ConfigDict(extra="ignore")

    code: str | None = None
    state: str | None = None
    error: str | None = None

    @property
    def has_code(self) -> bool:
        return self.code is not None

    @property
    def has_state(self) -> bool:
        return self.state is not None

    @property
    def has_error(self) -> bool:
        return self.error is not None

    @property
    def looks_like_patreon_redirect(self) -> bool:
        """A genuine Patreon redirect always carries at least one of code/state/error."""
        return self.has_code or self.has_state or self.has_error

    @property
    def has_required_params(self) -> bool:
        return self.has_code and self.has_state


class CallbackOutcome(Enum):
    """How the callback inputs classify — decided once by ``resolve_callback``."""

    BARE = auto()
    PATREON_ERROR = auto()
    MISSING_PARAMS = auto()
    STATE_EXPIRED = auto()
    STATE_INVALID = auto()
    VALID = auto()


@dataclass(frozen=True)
class ResolvedCallback:
    """A classified callback plus the diagnostics each arm needs to log and render.

    Nothing here identifies a Telegram user: the OAuth leg is anonymous, so the only thing the
    callback learns about identity is the Patreon one it fetches with ``code``.
    """

    outcome: CallbackOutcome
    error: str | None = None
    state_age_seconds: int | None = None
    code: str | None = None


def resolve_callback(params: Annotated[PatreonCallbackParams, Query()]) -> ResolvedCallback:
    """Classify the callback inputs into one outcome. FastAPI dependency, pure of side effects and
    logging: it reads config only through the module accessors and maps decode failures to outcomes,
    never letting a ``PatreonState*`` exception escape as a 422.

    The bare-hit check comes first, before ``is_configured``, so a non-Patreon visit never reveals
    whether Patreon support is switched on.
    """
    if not params.looks_like_patreon_redirect:
        return ResolvedCallback(CallbackOutcome.BARE)
    if params.error is not None:
        return ResolvedCallback(CallbackOutcome.PATREON_ERROR, error=params.error)
    if not params.has_required_params:
        return ResolvedCallback(CallbackOutcome.MISSING_PARAMS)
    return resolve_state(params.code, params.state)


def resolve_state(code: str | None, state: str | None) -> ResolvedCallback:
    """Check the ``state`` token into a VALID outcome, or classify the failure by age."""
    assert code is not None and state is not None, "has_required_params guarantees both are set"
    config = patreon.current_config()
    try:
        oauth.validate_state(config, state)
    except PatreonStateExpired as expired:
        return ResolvedCallback(CallbackOutcome.STATE_EXPIRED, state_age_seconds=round(expired.age_seconds))
    except PatreonStateInvalid:
        return ResolvedCallback(CallbackOutcome.STATE_INVALID)
    return ResolvedCallback(CallbackOutcome.VALID, code=code)


def render_result_page(
    title: str, message: str, bot_username: str | None, *, actions: str | None = None, status_code: int = 200
) -> HTMLResponse:
    """Fill the branded result template with the given title/message and the call to action below it.

    ``actions`` is ready-to-embed markup for pages that need more than a plain link home (the pairing
    page renders a deep link plus the code). Everything it interpolates must already be escaped by its
    builder. Without it the page falls back to a bare "Open Mitup" link, omitted when the bot username
    is unknown so the card is never left with a dead button.
    """
    if actions is None:
        actions = f'<a class="cta" href="https://t.me/{bot_username}">Open Mitup</a>' if bot_username else ""
    page = RESULT_TEMPLATE.substitute(title=title, message=message, actions=actions, logo=LOGO_IMG)
    return HTMLResponse(content=page, status_code=status_code)


def pairing_actions(bot_username: str | None, code: str) -> str:
    """The pairing page's call to action: the deep-link button plus the code as selectable text.

    The button is what nearly everyone uses, but deep links do not survive every browser (in-app
    webviews in particular), so the equivalent command is spelled out underneath for anyone who has
    to type it into the chat by hand. Both are escaped: the code is base64url and the username is
    Telegram-controlled, but escaping keeps the guarantee at the template rather than in an argument.
    """
    command = html.escape(f"/start {pairing.PAIRING_DEEP_LINK_PREFIX}_{code}")
    if bot_username is None:
        return f'<p class="code-hint">Send this to the Mitup bot to finish:</p><p class="code">{command}</p>'
    link = html.escape(pairing.pairing_deep_link(bot_username, code), quote=True)
    return (
        f'<a class="cta" href="{link}">Finish in Telegram</a>'
        '<p class="code-hint">If the button does not open Telegram, send this to the bot instead:</p>'
        f'<p class="code">{command}</p>'
    )


def failure_page(
    reason: str,
    title: str,
    message: str,
    bot_username: str | None,
    *,
    stage: str,
    status_code: int,
    **log_fields: object,
) -> HTMLResponse:
    """Log a structured record of the failure (so support can trace it) and render its page."""
    log.info(
        "Patreon callback did not complete",
        stage=stage,
        outcome=reason,
        reason=reason,
        status_code=status_code,
        **log_fields,
    )
    return render_result_page(title, message, bot_username, status_code=status_code)


def patreon_error_page(error: str, bot_username: str | None) -> HTMLResponse:
    """Render the page for an ``error`` on the redirect. Patreon only tells us ``access_denied``
    (the user declined) apart from everything else, so we branch on that and otherwise place the
    failure on Patreon's side rather than the user's."""
    if error == "access_denied":
        return failure_page(
            "consent_denied",
            "Connection not approved",
            "It looks like the connection wasn't approved on Patreon, so nothing has changed on your "
            f"Mitup account. Whenever you're ready, {RETRY_HINT.lower()} to try again.",
            bot_username,
            stage="patreon_redirect",
            status_code=400,
            patreon_error=error,
        )
    return failure_page(
        "patreon_error",
        "Patreon couldn't finish connecting",
        "Patreon ran into a problem while connecting your account, so we couldn't complete the link. "
        f"This is on Patreon's side, not yours. {RETRY_HINT} to try again.",
        bot_username,
        stage="patreon_redirect",
        status_code=502,
        patreon_error=error,
    )


def bare_landing_page(bot_username: str | None) -> HTMLResponse:
    """Generic Mitup page for a non-Patreon visitor (crawler, scanner, uptime check) that hit the
    endpoint with no Patreon params. Returned as 404 to signal a dead end and keep the endpoint out of
    path scans — the status code and the HTML body are independent, so a 404 with a friendly page is
    fine. Logged at INFO with a distinct outcome so scanner traffic is visible but clearly not an error.
    """
    log.info(
        "Patreon callback bare hit",
        stage="entry",
        outcome="bare_landing",
        has_code=False,
        has_state=False,
        has_error=False,
    )
    return render_result_page(
        "Welcome to Mitup",
        "Mitup is a Telegram bot that helps you organize meetups. Create an event, share it in your "
        "chats, and let people RSVP in a tap. There's nothing to see on this page, but you can start "
        "organizing right from Telegram.",
        bot_username,
        status_code=404,
    )


def missing_params_page(params: PatreonCallbackParams, bot_username: str | None) -> HTMLResponse:
    """Partial Patreon hit: at least one of code/state present, but not both."""
    return failure_page(
        "missing_params",
        "This link is incomplete",
        "Some of the information Patreon should send back is missing, so we couldn't finish "
        f"connecting your account. You haven't done anything wrong. {RETRY_HINT} to start again.",
        bot_username,
        stage="entry",
        status_code=400,
        has_code=params.has_code,
        has_state=params.has_state,
    )


def state_expired_page(state_age_seconds: int, bot_username: str | None) -> HTMLResponse:
    """The token is authentic but past its TTL. ``clock_skew_suspected`` flags an age under the TTL,
    which means the validating clock is behind the minting clock rather than a genuinely old button."""
    return failure_page(
        "state_expired",
        "This link has expired",
        "For your security this link only works for a limited time, and this one has expired. You "
        f"haven't done anything wrong. {RETRY_HINT} to get a fresh link.",
        bot_username,
        stage="decode_state",
        status_code=400,
        state_age_seconds=state_age_seconds,
        state_ttl_seconds=oauth.STATE_TTL_SECONDS,
        clock_skew_suspected=state_age_seconds < oauth.STATE_TTL_SECONDS,
    )


def state_invalid_page(bot_username: str | None) -> HTMLResponse:
    """The token failed signature validation (tampered, truncated, or signed with a different key)."""
    return failure_page(
        "state_invalid",
        "We couldn't verify this link",
        "We couldn't confirm this link came from Mitup, so we didn't connect anything. You haven't "
        f"done anything wrong. {RETRY_HINT} to try again.",
        bot_username,
        stage="decode_state",
        status_code=400,
    )


@router.get("/patreon/callback")
async def patreon_callback(
    ptb_app: Annotated[Application, Depends(get_ptb_application)],
    metrics_client: Annotated[MetricsClient, Depends(get_metrics_client)],
    params: Annotated[PatreonCallbackParams, Query()],
    resolved: Annotated[ResolvedCallback, Depends(resolve_callback)],
) -> HTMLResponse:
    """Bind the per-request logging context, log entry, then render the resolved outcome.

    ``resolve_callback`` has already classified the inputs; this handler only binds ``flow`` /
    ``request_id`` (so every downstream line carries them via merge_contextvars), logs the entry line,
    and hands off to the match-and-render layer.
    """
    with structlog.contextvars.bound_contextvars(flow=OAUTH_FLOW, request_id=uuid.uuid4().hex[:8]):
        log.info(
            "Patreon callback received",
            stage="entry",
            has_code=params.has_code,
            has_state=params.has_state,
            has_error=params.has_error,
        )
        # Funnel entry: any genuine Patreon redirect (consent granted or denied) counts as a link
        # attempt; bare scanner hits do not.
        if resolved.outcome is not CallbackOutcome.BARE:
            metrics_client.emit_feature(Feature.PATREON_LINK, name=MetricKey.FLOW_STARTED)
        return await render_resolved_callback(ptb_app, metrics_client, params, resolved)


async def render_resolved_callback(
    ptb_app: Application, metrics_client: MetricsClient, params: PatreonCallbackParams, resolved: ResolvedCallback
) -> HTMLResponse:
    """Run the side-effecting VALID path (token exchange + staging), or render a terminal page."""
    if resolved.outcome is not CallbackOutcome.VALID:
        return render_terminal_page(params, resolved, ptb_app.bot.username)

    assert resolved.code is not None, "VALID carries the authorization code"
    return await exchange_and_stage(ptb_app, metrics_client, resolved.code)


def render_terminal_page(
    params: PatreonCallbackParams, resolved: ResolvedCallback, bot_username: str | None
) -> HTMLResponse:
    """Route a non-VALID outcome to its logged, rendered browser page."""
    match resolved.outcome:
        case CallbackOutcome.BARE:
            return bare_landing_page(bot_username)
        case CallbackOutcome.PATREON_ERROR:
            assert resolved.error is not None, "PATREON_ERROR carries the error string"
            return patreon_error_page(resolved.error, bot_username)
        case CallbackOutcome.MISSING_PARAMS:
            return missing_params_page(params, bot_username)
        case CallbackOutcome.STATE_EXPIRED:
            assert resolved.state_age_seconds is not None, "STATE_EXPIRED carries the token age"
            return state_expired_page(resolved.state_age_seconds, bot_username)
        case CallbackOutcome.STATE_INVALID:
            return state_invalid_page(bot_username)
        case CallbackOutcome.VALID:
            raise AssertionError("VALID is handled by render_resolved_callback, never reaches here")


async def exchange_and_stage(ptb_app: Application, metrics_client: MetricsClient, code: str) -> HTMLResponse:
    """Exchange the authorization code, read the Patreon identity, and park it as a pending link.

    This is where the flow deliberately stops short of granting anything. The identity is proven, but
    the browser holding it is not proof of a Telegram account, so the result is a pairing code shown
    on the page and nothing else. Staging needs no Telegram fan-out, so it runs in a plain
    transaction rather than the write lifecycle.
    """
    bot_username = ptb_app.bot.username
    config = patreon.current_config()
    try:
        async with PatreonClient(config) as client:
            pair = await client.exchange_code(code)
            identity = await client.fetch_identity(pair.access_token)
    except (PatreonApiError, PatreonTokenRevoked) as err:
        log.exception(
            "Patreon token or identity exchange failed", stage="token_exchange", error_type=type(err).__name__
        )
        return failure_page(
            "patreon_api_error",
            "Patreon didn't respond in time",
            "We couldn't reach Patreon to confirm your account just now. This is usually temporary and "
            f"on Patreon's side, not yours. {RETRY_HINT} to try again in a few minutes.",
            bot_username,
            stage="token_exchange",
            status_code=502,
        )

    is_active_member = identity.is_active_member_of(config.campaign_id)
    # An active member maps to their entitled tier; a non-member maps to NONE. level_for_amount floors
    # at SUPPORTER for any active member, so it must only see amounts of members already known active.
    level = (
        supporter.level_for_amount(identity.entitled_amount_cents_of(config.campaign_id), config)
        if is_active_member
        else SupporterLevel.NONE
    )
    log.info(
        "Patreon identity fetched",
        stage="identity_fetch",
        patreon_user_id=identity.patreon_user_id,
        is_active_member=is_active_member,
        supporter_level=level.value,
    )

    async with db.begin() as session:
        pairing_code = await stage_pending_link(
            session,
            patreon_user_id=identity.patreon_user_id,
            patreon_full_name=identity.full_name,
            supporter_level=level,
        )
    log.info(
        "Pending Patreon link staged",
        stage="stage_pending_link",
        outcome="pending_link_staged",
        patreon_user_id=identity.patreon_user_id,
        # Whether the confirmation prompt will be able to name the account or fall back to its id.
        has_display_name=identity.full_name is not None,
        supporter_level=level.value,
        expires_in_seconds=pairing.PAIRING_CODE_TTL_SECONDS,
    )
    return render_result_page(
        "One more step",
        "Your Patreon account checked out. To finish, open Mitup and confirm from your own Telegram "
        "account. That last step is what tells us which Mitup account to connect, so nothing is "
        "connected until you do it. The link below works once and expires shortly.",
        bot_username,
        actions=pairing_actions(bot_username, pairing_code),
    )


PATREON_SIGNATURE_HEADER = "X-Patreon-Signature"
PATREON_EVENT_HEADER = "X-Patreon-Event"
WEBHOOK_FLOW = "patreon_membership_webhook"


class WebhookApplied(Enum):
    """What a verified membership event changed, so the endpoint meters and notifies consistently."""

    UPGRADED = auto()
    DOWNGRADED = auto()
    GRACE_STARTED = auto()
    UNCHANGED = auto()


class SignatureVerdict(StrEnum):
    """How a delivery's signature check ended. The three failures are one 403 to the caller and three
    different operator answers: we never registered a webhook, somebody is probing us unsigned, or a
    signed request did not match our key."""

    VALID = "valid"
    NO_SECRET_REGISTERED = "no_secret_registered"
    MISSING_SIGNATURE_HEADER = "missing_signature_header"
    DIGEST_MISMATCH = "digest_mismatch"


class LevelReason(StrEnum):
    """Which rule decided the tier a membership event maps to."""

    DELETE_TRIGGER = "delete_trigger"
    NOT_ACTIVE_PATRON = "not_active_patron"
    ENTITLED_AMOUNT = "entitled_amount"


class ChangeReason(StrEnum):
    """Why the applied transition landed where it did."""

    ALREADY_AT_TARGET_LEVEL = "already_at_target_level"
    LEVEL_HELD_BY_GRANT = "level_held_by_grant"
    NOTHING_TO_LOSE = "nothing_to_lose"
    TIER_UPGRADE = "tier_upgrade"
    TIER_DOWNGRADE = "tier_downgrade"
    MEMBERSHIP_LOST_GRACE_OPENED = "membership_lost_grace_opened"


@dataclass(frozen=True)
class TargetLevel:
    """The tier an event maps to, carrying the rule that chose it."""

    level: SupporterLevel
    reason: LevelReason


@dataclass(frozen=True)
class MembershipTransition:
    """What applying an event changed, and why. ``UNCHANGED`` covers unrelated situations — an
    event landing on the tier the user already holds, a tier propped up by the manually-granted
    floor, and a loss for somebody with nothing to lose — so the outcome alone cannot answer a
    support question about it."""

    applied: WebhookApplied
    reason: ChangeReason


def signature_verdict(secret: str | None, raw_body: bytes, signature: str | None) -> SignatureVerdict:
    """Constant-time check of Patreon's ``X-Patreon-Signature`` (HMAC-MD5 of the exact raw body bytes).

    A missing secret (no webhook registered yet) or a missing header fails closed. MD5 is not our
    choice — it is the algorithm Patreon signs deliveries with — so this is not a security downgrade.
    """
    if secret is None:
        return SignatureVerdict.NO_SECRET_REGISTERED
    if signature is None:
        return SignatureVerdict.MISSING_SIGNATURE_HEADER
    expected = hmac.new(secret.encode(), raw_body, hashlib.md5).hexdigest()
    if not secret_header_matches(signature, expected):
        return SignatureVerdict.DIGEST_MISMATCH
    return SignatureVerdict.VALID


def target_level(trigger: str | None, member: MemberResource, config: PatreonConfig) -> TargetLevel:
    """The tier a membership event maps to: NONE for a delete or any non-active member (a loss, which
    starts cancellation grace — see ``apply_membership_transition``), otherwise the tier their entitled
    amount reaches via the central policy."""
    if trigger == MEMBER_DELETE_TRIGGER:
        return TargetLevel(SupporterLevel.NONE, LevelReason.DELETE_TRIGGER)
    if not member.is_active_patron:
        return TargetLevel(SupporterLevel.NONE, LevelReason.NOT_ACTIVE_PATRON)
    level = supporter.level_for_amount(member.attributes.currently_entitled_amount_cents, config)
    return TargetLevel(level, LevelReason.ENTITLED_AMOUNT)


def apply_membership_transition(
    user: User, subscription: SupporterSubscription, target: SupporterLevel
) -> MembershipTransition:
    """Apply ``target`` to the user and reconcile the subscription runway. Returns what changed.

    A gain (``target`` is a paying tier) applies instantly, clamped to the manually-granted floor:
    on a level change it refreshes the grace runway; an event landing on the level the user already
    holds changes nothing. A loss (``target`` is NONE — a decline, former patron, or delete) does NOT
    cut perks off; instead it keeps the user's current level and opens a cancellation grace window,
    marking the row already-notified so the daily job goes straight to revoke when the window elapses
    (no duplicate grace message). A loss for a user with nothing to lose — at NONE already, or whose
    granted floor covers everything they hold — is a no-op."""
    previous = user.supporter_level
    if supporter.is_supporter(target):
        # Gain or between-tier change: apply the entitled tier instantly.
        effective = supporter.highest(target, user.granted_supporter_level)
        if previous == effective:
            reason = ChangeReason.ALREADY_AT_TARGET_LEVEL if effective is target else ChangeReason.LEVEL_HELD_BY_GRANT
            return MembershipTransition(WebhookApplied.UNCHANGED, reason)
        user.supporter_level = effective
        subscription.support_expiration = dt.datetime.now(dt.UTC) + dt.timedelta(days=SUPPORT_GRACE_DAYS)
        subscription.expiration_notified = False
        if supporter.meets(previous, effective):
            # A drop to a lower paying tier: adjust silently.
            return MembershipTransition(WebhookApplied.DOWNGRADED, ChangeReason.TIER_DOWNGRADE)
        return MembershipTransition(WebhookApplied.UPGRADED, ChangeReason.TIER_UPGRADE)

    # target is NONE: membership loss. Give our own grace rather than cutting perks now.
    if supporter.meets(user.granted_supporter_level, previous):
        # Nothing to lose — the granted floor already covers everything the user holds (which
        # includes a user sitting at NONE), so no grace window and no revoke are needed.
        return MembershipTransition(WebhookApplied.UNCHANGED, ChangeReason.NOTHING_TO_LOSE)
    # Keep the current level (perks stay on) and let the daily job revoke when the window elapses.
    # expiration_notified=True so the daily due-flow revokes straight away rather than re-announcing grace.
    subscription.support_expiration = dt.datetime.now(dt.UTC) + dt.timedelta(days=SUPPORT_GRACE_DAYS)
    subscription.expiration_notified = True
    return MembershipTransition(WebhookApplied.GRACE_STARTED, ChangeReason.MEMBERSHIP_LOST_GRACE_OPENED)


async def notify_membership_change(api: TelegramApiWrapper, user: User, outcome: WebhookApplied):
    """Send the DM matching the transition; silent only for a no-op (unchanged)."""
    match outcome:
        case WebhookApplied.UPGRADED:
            message = SupporterNotificationMessages.unlocked_for(user.supporter_level)
            await api.send_message_to_user(user, message.get(lang=user.lang))
        case WebhookApplied.DOWNGRADED:
            message = SupporterNotificationMessages.downgraded_to(user.supporter_level)
            await api.send_message_to_user(user, message.get(lang=user.lang))
        case WebhookApplied.GRACE_STARTED:
            # The catalog copy phrases the window in days (``${days}``), fed from SUPPORT_GRACE_DAYS so
            # the number in the message can never drift from the expiry math above.
            await api.send_message_to_user(
                user,
                SupporterNotificationMessages.SUPPORT_ENDED_GRACE.get(lang=user.lang, days=SUPPORT_GRACE_DAYS),
            )
        case WebhookApplied.UNCHANGED:
            ...


async def apply_membership_event(
    api: TelegramApiWrapper, trigger: str | None, payload: WebhookMemberPayload
) -> WebhookApplied:
    """Resolve the linked user by ``patreon_user_id`` and apply the event's target level, sending the
    matching notification. An event for a patron we don't track (no linked subscription) is a benign
    no-op — we return 200 so Patreon does not retry a delivery there is nothing to do about."""
    member = payload.data
    patreon_user_id = member.patreon_user_id
    if patreon_user_id is None:
        log.info("Patreon webhook member without a user relationship", stage="resolve", trigger=trigger)
        return WebhookApplied.UNCHANGED

    config = patreon.current_config()
    target = target_level(trigger, member, config)

    async with db.begin_write(api) as session:
        subscription = (
            await session.exec(
                select(SupporterSubscription).where(SupporterSubscription.patreon_user_id == patreon_user_id)
            )
        ).first()
        if subscription is None:
            log.info(
                "Patreon webhook for an untracked patron",
                stage="resolve",
                trigger=trigger,
                patreon_user_id=patreon_user_id,
            )
            return WebhookApplied.UNCHANGED
        user = (await session.exec(select(User).where(User.id == subscription.user_id))).first()
        if user is None:
            log.warning(
                "Patreon webhook subscription without a user",
                stage="resolve",
                trigger=trigger,
                patreon_user_id=patreon_user_id,
                user_id=subscription.user_id,
            )
            return WebhookApplied.UNCHANGED

        previous_level = user.supporter_level
        transition = apply_membership_transition(user, subscription, target.level)
        await notify_membership_change(api, user, transition.applied)
        # Re-admit only on a genuine reactivation (was not a supporter, now is); tier-to-tier moves
        # between host levels leave any existing group membership untouched.
        if not supporter.is_supporter(previous_level) and supporter.is_supporter(user.supporter_level):
            await readmit_to_hosts_group(api, user, trigger=HostsGroupTrigger.WEBHOOK)
        log.info(
            "Patreon webhook applied",
            stage="apply",
            trigger=trigger,
            patreon_user_id=patreon_user_id,
            tg_user_id=user.tg_user_id,
            previous_level=previous_level.value,
            # The event's target tier and the user's actual level after applying it — these differ on a
            # loss, where we keep the level and open a grace window instead of dropping to the target.
            target_level=target.level.value,
            supporter_level=user.supporter_level.value,
            outcome=transition.applied.name.lower(),
            level_reason=str(target.reason),
            change_reason=str(transition.reason),
            support_expiration=subscription.support_expiration,
        )
        return transition.applied


@router.post("/patreon/webhook")
async def patreon_webhook(
    request: Request,
    ptb_app: Annotated[Application, Depends(get_ptb_application)],
    metrics_client: Annotated[MetricsClient, Depends(get_metrics_client)],
) -> Response:
    """Verify a Patreon membership delivery and apply it to the linked user.

    Status-code contract: **403** on a missing/invalid signature (no write), **400** on an unparseable
    body, **200** on a successful apply or a benign no-op (unknown patron). Our own processing failures
    surface as **500** so Patreon's retry queue redelivers; the ``except`` only meters the fault before
    re-raising, so that contract is unchanged. The signature is checked against the exact raw bytes,
    before any JSON re-parse.

    The two fault metrics are emitted as continuous 0/1 series: ``FORBIDDEN`` and ``FAULT`` each emit
    ``0`` on the path that clears them, so a healthy endpoint is visible in CloudWatch, not just a
    failing one. What the delivery carried and what it changed are answered on the log plane, by the
    ``Patreon webhook received``, ``Patreon webhook applied`` and resolve-stage no-op lines.
    """
    with structlog.contextvars.bound_contextvars(flow=WEBHOOK_FLOW, request_id=uuid.uuid4().hex[:8]):
        raw_body = await request.body()
        trigger = request.headers.get(PATREON_EVENT_HEADER)
        signature = request.headers.get(PATREON_SIGNATURE_HEADER)
        log.info("Patreon webhook received", stage="receive", trigger=trigger, signed=signature is not None)

        secret = await webhooks.load_webhook_secret()
        verdict = signature_verdict(secret, raw_body, signature)
        if verdict is not SignatureVerdict.VALID:
            metrics_client.emit(MetricKey.PATREON_WEBHOOK_FORBIDDEN)
            client_host = request.client.host if request.client is not None else "unknown"
            log.warning(
                "Rejected Patreon webhook, invalid or missing signature",
                stage="verify",
                reason=str(verdict),
                trigger=trigger,
                client_host=client_host,
                body_bytes=len(raw_body),
            )
            raise HTTPException(status_code=403)
        # Signature valid: emit the 0-baseline so FORBIDDEN is a continuous 0/1 series, not failure-only.
        metrics_client.emit(MetricKey.PATREON_WEBHOOK_FORBIDDEN, 0)
        log.info("Patreon webhook signature verified", stage="verify", outcome="valid", trigger=trigger)

        try:
            payload = WebhookMemberPayload.model_validate_json(raw_body)
        except ValidationError:
            log.warning("Malformed Patreon webhook payload", stage="parse", trigger=trigger)
            return Response(status_code=400)

        api = build_api(BotAdapter(ptb_app.bot, metrics_client))
        try:
            await apply_membership_event(api, trigger, payload)
        except Exception:
            # Processing faults deliberately surface as 500 (uncaught → Patreon retries). There is no
            # generic 500-fault metric on the web app, so this counter is the fault's only CloudWatch
            # trace. Emit 1 and re-raise to preserve the 500 + retry contract.
            metrics_client.emit(MetricKey.PATREON_WEBHOOK_FAULT)
            log.exception("Patreon webhook processing failed", stage="apply", trigger=trigger)
            raise
        # Applied without fault: the 0-baseline keeps FAULT a continuous 0/1 series.
        metrics_client.emit(MetricKey.PATREON_WEBHOOK_FAULT, 0)
        return Response(status_code=200)
