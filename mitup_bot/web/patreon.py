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
from enum import Enum, auto
from pathlib import Path
from string import Template
from typing import Annotated, assert_never

import structlog
from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram.ext import Application

from mitup_bot import db, patreon
from mitup_bot.api_wrapper import BotAdapter, TelegramApiWrapper, build_api
from mitup_bot.exceptions import PatreonApiError, PatreonStateExpired, PatreonStateInvalid, PatreonTokenRevoked
from mitup_bot.models import PremiumSubscription, User
from mitup_bot.monitoring.client import MetricsClient
from mitup_bot.patreon import PatreonClient, TokenPair, oauth
from mitup_bot.utils.messages import CollaborateMessages
from mitup_bot.web.dependencies import get_metrics_client, get_ptb_application

log = structlog.get_logger(__name__)

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


def render_result_page(title: str, message: str, bot_username: str | None, *, status_code: int = 200) -> HTMLResponse:
    """Fill the branded result template with the given title/message and a link back to the bot."""
    return_link = f'<a class="cta" href="https://t.me/{bot_username}">Open Mitup</a>' if bot_username else ""
    html = RESULT_TEMPLATE.substitute(title=title, message=message, return_link=return_link)
    return HTMLResponse(content=html, status_code=status_code)


def failure_page(
    reason: str, title: str, message: str, bot_username: str | None, *, status_code: int, **log_fields: object
) -> HTMLResponse:
    """Log a structured record of the failure (so support can trace it) and render its page."""
    log.info("Patreon callback did not complete", reason=reason, status_code=status_code, **log_fields)
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
            status_code=400,
            patreon_error=error,
        )
    return failure_page(
        "patreon_error",
        "Patreon couldn't finish connecting",
        "Patreon ran into a problem while connecting your account, so we couldn't complete the link. "
        f"This is on Patreon's side, not yours. {RETRY_HINT} to try again.",
        bot_username,
        status_code=502,
        patreon_error=error,
    )


@router.get("/patreon/callback")
async def patreon_callback(
    ptb_app: Annotated[Application, Depends(get_ptb_application)],
    metrics_client: Annotated[MetricsClient, Depends(get_metrics_client)],
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> HTMLResponse:
    bot_username = ptb_app.bot.username

    if not patreon.is_configured():
        # Defensive: no valid link can exist without config, but the route is always registered.
        return failure_page(
            "unconfigured",
            "Patreon isn't available yet",
            "Supporting Mitup through Patreon isn't switched on yet. Nothing went wrong on your end. "
            "Please try again later.",
            bot_username,
            status_code=503,
        )

    if error is not None:
        return patreon_error_page(error, bot_username)

    if code is None or state is None:
        return failure_page(
            "missing_params",
            "This link is incomplete",
            "Some of the information Patreon should send back is missing, so we couldn't finish "
            f"connecting your account. You haven't done anything wrong. {RETRY_HINT} to start again.",
            bot_username,
            status_code=400,
        )

    config = patreon.current_config()
    try:
        tg_user_id = oauth.decode_state(config, state)
    except PatreonStateExpired:
        return failure_page(
            "state_expired",
            "This link has expired",
            "For your security this link only works for a few minutes, and this one has expired. You "
            f"haven't done anything wrong. {RETRY_HINT} to get a fresh link.",
            bot_username,
            status_code=400,
        )
    except PatreonStateInvalid:
        return failure_page(
            "state_invalid",
            "We couldn't verify this link",
            "We couldn't confirm this link came from Mitup, so we didn't connect anything. You haven't "
            f"done anything wrong. {RETRY_HINT} to try again.",
            bot_username,
            status_code=400,
        )

    try:
        async with PatreonClient(config) as client:
            pair = await client.exchange_code(code)
            identity = await client.fetch_identity(pair.access_token)
    except PatreonApiError, PatreonTokenRevoked:
        log.exception("Patreon token or identity exchange failed", tg_user_id=tg_user_id)
        return failure_page(
            "patreon_api_error",
            "Patreon didn't respond in time",
            "We couldn't reach Patreon to confirm your account just now. This is usually temporary and "
            f"on Patreon's side, not yours. {RETRY_HINT} to try again in a few minutes.",
            bot_username,
            status_code=502,
            tg_user_id=tg_user_id,
        )

    api = build_api(BotAdapter(ptb_app.bot, metrics_client))
    outcome = await link_patreon_account(
        api,
        tg_user_id,
        pair,
        patreon_user_id=identity.patreon_user_id,
        is_active_member=identity.is_active_member_of(config.campaign_id),
    )
    return result_page_for(outcome, bot_username)


async def link_patreon_account(
    api: TelegramApiWrapper, tg_user_id: int, pair: TokenPair, *, patreon_user_id: str, is_active_member: bool
) -> LinkOutcome:
    """Upsert the subscription and, on success, queue the confirmation message to the user.

    A re-link during the revoke grace period updates the existing row in place and clears
    ``revoked_time``, so the user never has to unlink first. The write and the send run inside
    ``begin_write`` so the message drains only after the row is committed.
    """
    async with db.begin_write(api) as session:
        # The callback only needs the user's own columns (id, lang, is_premium); load_collections=False
        # skips the meetups/joined_links selectin queries. Do not touch those collections here.
        user = await User.by_tg_user_id(session, tg_user_id, load_collections=False)
        if user is None:
            log.warning("Patreon callback for an unknown Telegram user", tg_user_id=tg_user_id)
            return LinkOutcome.UNKNOWN_USER

        claimed_elsewhere = (
            await session.exec(
                select(PremiumSubscription).where(PremiumSubscription.patreon_user_id == patreon_user_id)
            )
        ).first()
        if claimed_elsewhere is not None and claimed_elsewhere.user_id != user.db_id:
            log.warning(
                "Patreon account already linked to another Telegram user",
                patreon_user_id=patreon_user_id,
                tg_user_id=tg_user_id,
                linked_user_id=claimed_elsewhere.user_id,
            )
            return LinkOutcome.ALREADY_LINKED_ELSEWHERE

        subscription = await upsert_subscription(session, user, pair, patreon_user_id)

        if is_active_member:
            user.is_premium = True
            subscription.premium_expiration = dt.datetime.now(dt.UTC) + dt.timedelta(days=PREMIUM_GRACE_DAYS)
            message = CollaborateMessages.LINK_CONFIRMED_PREMIUM
            outcome = LinkOutcome.LINKED_PREMIUM
        else:
            user.is_premium = False
            subscription.premium_expiration = None
            message = CollaborateMessages.LINK_CONFIRMED_NO_PATRON
            outcome = LinkOutcome.LINKED_NO_PATRON

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
