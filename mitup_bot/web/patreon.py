"""The Patreon OAuth callback endpoint and its browser result pages.

Patreon redirects the user's browser here after the consent screen, so unlike ``/telegram`` this
route renders HTML for a human and deep-links back to the bot chat. It owns its own DB and api
plumbing: there is no per-request session dependency, so it builds an api from the PTB bot and runs
the write plus the confirmation send through ``db.begin_write`` (capture, commit, drain), exactly
like the CLI batch jobs.

The page markup lives in ``templates/patreon_result.html`` (a Mitup-branded shell filled via
``string.Template``); only the per-outcome title and message live here. This copy is intentionally
plain, hardcoded English rather than going through the message/translation pipeline: the pages render
in a browser with no ``lang`` context (the failure pages fire before we can even resolve the user),
and there is no gettext catalog for HTML. The Telegram confirmation the user receives back in the
chat *does* go through the translated pipeline. Every failure render is logged with enough context to
trace a support question back to its cause.
"""

import datetime as dt
import uuid
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from string import Template
from typing import Annotated, assert_never

import structlog
from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram.ext import Application

from mitup_bot import db, patreon, supporter
from mitup_bot.api_wrapper import BotAdapter, TelegramApiWrapper, build_api
from mitup_bot.exceptions import PatreonApiError, PatreonStateExpired, PatreonStateInvalid, PatreonTokenRevoked
from mitup_bot.models import PremiumSubscription, User
from mitup_bot.monitoring.client import MetricsClient
from mitup_bot.patreon import PatreonClient, TokenPair, oauth
from mitup_bot.supporter import SupporterLevel
from mitup_bot.utils.messages import CollaborateMessages
from mitup_bot.web.dependencies import get_metrics_client, get_ptb_application

log = structlog.get_logger(__name__)

# Stable machine key bound on every log line of a single callback invocation, so the whole OAuth
# round-trip can be filtered as one series in CloudWatch Logs Insights.
OAUTH_FLOW = "patreon_oauth_callback"

router = APIRouter()

# A freshly linked patron gets premium immediately with a short runway; the daily job (#158)
# extends it while the pledge stays active and lets it lapse otherwise.
PREMIUM_GRACE_DAYS = 7

RESULT_TEMPLATE = Template((Path(__file__).parent / "templates" / "patreon_result.html").read_text(encoding="utf-8"))

# Reused across the failure pages: every one points the user back to the same in-bot button.
RETRY_HINT = "Head back to Mitup and tap Link Patreon account in the Collaborate menu"


class LinkOutcome(Enum):
    """Result of persisting the link, used to pick the browser page shown to the user."""

    LINKED_PREMIUM = auto()
    LINKED_NO_PATRON = auto()
    UNKNOWN_USER = auto()
    ALREADY_LINKED_ELSEWHERE = auto()


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
    UNCONFIGURED = auto()
    PATREON_ERROR = auto()
    MISSING_PARAMS = auto()
    STATE_EXPIRED = auto()
    STATE_INVALID = auto()
    VALID = auto()


@dataclass(frozen=True)
class ResolvedCallback:
    """A classified callback plus the diagnostics each arm needs to log and render."""

    outcome: CallbackOutcome
    tg_user_id: int | None = None
    error: str | None = None
    state_age_seconds: int | None = None
    code: str | None = None
    state: str | None = None


def resolve_callback(params: Annotated[PatreonCallbackParams, Query()]) -> ResolvedCallback:
    """Classify the callback inputs into one outcome. FastAPI dependency, pure of side effects and
    logging: it reads config only through the module accessors and maps decode failures to outcomes,
    never letting a ``PatreonState*`` exception escape as a 422.

    The bare-hit check comes first, before ``is_configured``, so a non-Patreon visit never reveals
    whether Patreon support is switched on.
    """
    if not params.looks_like_patreon_redirect:
        return ResolvedCallback(CallbackOutcome.BARE)
    if not patreon.is_configured():
        return ResolvedCallback(CallbackOutcome.UNCONFIGURED)
    if params.error is not None:
        return ResolvedCallback(CallbackOutcome.PATREON_ERROR, error=params.error)
    if not params.has_required_params:
        return ResolvedCallback(CallbackOutcome.MISSING_PARAMS)
    return resolve_state(params.code, params.state)


def resolve_state(code: str | None, state: str | None) -> ResolvedCallback:
    """Decode the ``state`` token into a VALID outcome, or classify the decode failure by age."""
    assert code is not None and state is not None, "has_required_params guarantees both are set"
    config = patreon.current_config()
    try:
        tg_user_id = oauth.decode_state(config, state)
    except PatreonStateExpired as expired:
        return ResolvedCallback(CallbackOutcome.STATE_EXPIRED, state_age_seconds=round(expired.age_seconds))
    except PatreonStateInvalid:
        return ResolvedCallback(CallbackOutcome.STATE_INVALID)
    return ResolvedCallback(CallbackOutcome.VALID, tg_user_id=tg_user_id, code=code, state=state)


def render_result_page(title: str, message: str, bot_username: str | None, *, status_code: int = 200) -> HTMLResponse:
    """Fill the branded result template with the given title/message and a link back to the bot."""
    return_link = f'<a class="cta" href="https://t.me/{bot_username}">Open Mitup</a>' if bot_username else ""
    html = RESULT_TEMPLATE.substitute(title=title, message=message, return_link=return_link)
    return HTMLResponse(content=html, status_code=status_code)


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
    """Log a structured record of the failure (so support can trace it) and render its page.

    ``reason`` is kept for existing log consumers; ``outcome`` mirrors it as the new stable key that
    the happy-path line also carries, so success and failure share one filterable series.
    """
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


def unconfigured_page(bot_username: str | None) -> HTMLResponse:
    """Defensive: no valid link can exist without config, but the route is always registered."""
    return failure_page(
        "unconfigured",
        "Patreon isn't available yet",
        "Supporting Mitup through Patreon isn't switched on yet. Nothing went wrong on your end. "
        "Please try again later.",
        bot_username,
        stage="entry",
        status_code=503,
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
        return await render_resolved_callback(ptb_app, metrics_client, params, resolved)


async def render_resolved_callback(
    ptb_app: Application, metrics_client: MetricsClient, params: PatreonCallbackParams, resolved: ResolvedCallback
) -> HTMLResponse:
    """Run the side-effecting VALID path (token exchange + link), or render a terminal page."""
    if resolved.outcome is not CallbackOutcome.VALID:
        return render_terminal_page(params, resolved, ptb_app.bot.username)

    assert resolved.tg_user_id is not None and resolved.code is not None, "VALID carries tg_user_id and code"
    # The initiator is now known; every later line carries it.
    structlog.contextvars.bind_contextvars(tg_user_id=resolved.tg_user_id)
    return await exchange_and_link(ptb_app, metrics_client, resolved.tg_user_id, resolved.code)


def render_terminal_page(
    params: PatreonCallbackParams, resolved: ResolvedCallback, bot_username: str | None
) -> HTMLResponse:
    """Route a non-VALID outcome to its logged, rendered browser page."""
    match resolved.outcome:
        case CallbackOutcome.BARE:
            return bare_landing_page(bot_username)
        case CallbackOutcome.UNCONFIGURED:
            return unconfigured_page(bot_username)
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


async def exchange_and_link(
    ptb_app: Application, metrics_client: MetricsClient, tg_user_id: int, code: str
) -> HTMLResponse:
    """Exchange the code, read the user's identity, and persist the link."""
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

    api = build_api(BotAdapter(ptb_app.bot, metrics_client))
    outcome = await link_patreon_account(
        api, tg_user_id, pair, patreon_user_id=identity.patreon_user_id, supporter_level=level
    )
    return result_page_for(outcome, bot_username)


async def link_patreon_account(
    api: TelegramApiWrapper, tg_user_id: int, pair: TokenPair, *, patreon_user_id: str, supporter_level: SupporterLevel
) -> LinkOutcome:
    """Upsert the subscription and, on success, queue the confirmation message to the user.

    A re-link during the revoke grace period updates the existing row in place and clears
    ``revoked_time``, so the user never has to unlink first. The write and the send run inside
    ``begin_write`` so the message drains only after the row is committed.
    """
    async with db.begin_write(api) as session:
        # The callback only needs the user's own columns (id, lang, supporter_level);
        # load_collections=False skips the meetups/joined_links selectin queries. Do not touch those.
        user = await User.by_tg_user_id(session, tg_user_id, load_collections=False)
        if user is None:
            log.warning(
                "Patreon callback for an unknown Telegram user",
                flow=OAUTH_FLOW,
                stage="persist",
                outcome=LinkOutcome.UNKNOWN_USER.name.lower(),
                tg_user_id=tg_user_id,
            )
            return LinkOutcome.UNKNOWN_USER

        claimed_elsewhere = (
            await session.exec(
                select(PremiumSubscription).where(PremiumSubscription.patreon_user_id == patreon_user_id)
            )
        ).first()
        if claimed_elsewhere is not None and claimed_elsewhere.user_id != user.db_id:
            log.warning(
                "Patreon account already linked to another Telegram user",
                flow=OAUTH_FLOW,
                stage="persist",
                outcome=LinkOutcome.ALREADY_LINKED_ELSEWHERE.name.lower(),
                patreon_user_id=patreon_user_id,
                tg_user_id=tg_user_id,
                linked_user_id=claimed_elsewhere.user_id,
            )
            return LinkOutcome.ALREADY_LINKED_ELSEWHERE

        subscription = await upsert_subscription(session, user, pair, patreon_user_id)

        if supporter.is_supporter(supporter_level):
            user.supporter_level = supporter_level
            subscription.premium_expiration = dt.datetime.now(dt.UTC) + dt.timedelta(days=PREMIUM_GRACE_DAYS)
            message = CollaborateMessages.LINK_CONFIRMED_PREMIUM
            outcome = LinkOutcome.LINKED_PREMIUM
        else:
            user.supporter_level = SupporterLevel.NONE
            subscription.premium_expiration = None
            message = CollaborateMessages.LINK_CONFIRMED_NO_PATRON
            outcome = LinkOutcome.LINKED_NO_PATRON

        log.info(
            "Patreon account linked",
            flow=OAUTH_FLOW,
            stage="persist",
            outcome=outcome.name.lower(),
            tg_user_id=tg_user_id,
            patreon_user_id=patreon_user_id,
            supporter_level=supporter_level.value,
        )
        await api.send_message_to_user(user, message.get(lang=user.lang))
        return outcome


async def upsert_subscription(
    session: AsyncSession, user: User, pair: TokenPair, patreon_user_id: str
) -> PremiumSubscription:
    """Create the user's subscription row, or update the existing one in place.

    A re-link during the revoke grace period updates the existing row and clears ``revoked_time``,
    so a returning user never has to unlink first.
    """
    subscription = (
        await session.exec(select(PremiumSubscription).where(PremiumSubscription.user_id == user.db_id))
    ).first()
    if subscription is None:
        subscription = PremiumSubscription(
            user_id=user.db_id,
            patreon_user_id=patreon_user_id,
            access_token=pair.access_token,
            refresh_token=pair.refresh_token,
            token_expiration=pair.expires_at,
        )
        session.add(subscription)
        return subscription

    subscription.patreon_user_id = patreon_user_id
    subscription.access_token = pair.access_token
    subscription.refresh_token = pair.refresh_token
    subscription.token_expiration = pair.expires_at
    # A re-link clears any pending revoke so the grace-period row becomes active again.
    subscription.revoked_time = None
    return subscription


def result_page_for(outcome: LinkOutcome, bot_username: str | None) -> HTMLResponse:
    match outcome:
        case LinkOutcome.LINKED_PREMIUM:
            return render_result_page(
                "You're all set",
                "Your Patreon account is connected and premium is active. Head back to Mitup to keep going.",
                bot_username,
            )
        case LinkOutcome.LINKED_NO_PATRON:
            return render_result_page(
                "Account connected",
                "Your Patreon account is connected. Become a patron to unlock premium, then head back to Mitup.",
                bot_username,
            )
        case LinkOutcome.UNKNOWN_USER:
            # link_patreon_account already logged this with the tg_user_id, so just render.
            return render_result_page(
                "We couldn't find your Mitup account",
                f"We couldn't match this to a Mitup account, so nothing was connected. {RETRY_HINT} to start again.",
                bot_username,
                status_code=400,
            )
        case LinkOutcome.ALREADY_LINKED_ELSEWHERE:
            # link_patreon_account already logged this with the tg_user_id and the linked user id.
            return render_result_page(
                "This Patreon is already linked",
                "This Patreon account is already connected to a different Mitup account. If that wasn't "
                "you, please reach out and we'll help sort it out.",
                bot_username,
                status_code=409,
            )
        case _ as unreachable:
            assert_never(unreachable)
