"""Turning a Patreon pairing code into a link, with a confirmation in between.

Holding a pairing code is not authority to link. The finish link can be passed on exactly like the
consent URL it replaced, and a tap on a stranger's link would overwrite a real Host's subscription
with an account they do not control, taking their tier with it. So the code only opens a prompt: it
claims the pending row against the sender, shows which Patreon account is about to be connected, and
writes nothing until that same account explicitly confirms.
"""

import functools
from collections.abc import Callable, Coroutine
from typing import Any, NoReturn, assert_never

import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.ext import ApplicationHandlerStop, filters

from mitup_bot import guards, patreon_link
from mitup_bot.db import with_session
from mitup_bot.exceptions import EffectiveUserNotSet
from mitup_bot.handlers.personal_filters import PatreonPairingStartFilter
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.handlers.start_payload import parse_start_payload
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import Feature, MetricKey
from mitup_bot.patreon import pairing, pending_links
from mitup_bot.patreon_link import LinkOutcome, RedemptionRefusal
from mitup_bot.translations import locale_for_language_code
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import PrivacyMessages
from mitup_bot.views.collaborate import (
    patreon_already_linked_elsewhere_view,
    patreon_link_code_not_valid_view,
    patreon_link_confirmation_view,
    patreon_link_declined_view,
    patreon_link_needs_setup_view,
)

from .enums import PATREON_PAIRING_HANDLERS_GROUP, CollaborateHandlerId
from .utils import build_collaborate_view, subscription_for_user

log = structlog.get_logger(__name__)


def claim_update[**P](
    func: Callable[P, Coroutine[Any, Any, object]],
) -> Callable[P, Coroutine[Any, Any, NoReturn]]:
    """Stop the update after the handler runs.

    The redemption `/start` binds ahead of both the onboarding conversation and the group-0 `/start`
    handler, so it must claim the updates it answers or they would be processed a second time.

    Must wrap OUTSIDE `with_session`: raising inside the session scope would roll back the claim.
    """

    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> NoReturn:
        await func(*args, **kwargs)
        raise ApplicationHandlerStop

    return wrapper


def refuse(context: TMitupContext, refusal: RedemptionRefusal, detail: str | None = None) -> None:
    """Meter a link attempt that ended without linking, so the funnel shows where people fall out.

    ``detail`` narrows a refusal whose user-facing message is deliberately vague, and rides as a
    metric property so the classification is something to alert on rather than something to grep.
    """
    outcome = refusal.name.lower() if detail is None else f"{refusal.name.lower()}:{detail}"
    context.put_feature_metric(
        Feature.PATREON_LINK, name=MetricKey.PATREON_LINK_REFUSED, properties={"outcome": outcome}
    )


async def refuse_unusable_code(
    session: AsyncSession, context: TMitupContext, code: str | None, tg_user_id: int, stage: str
) -> None:
    """Meter and log a code that could not be used, having already decided not to use it.

    The classification here NEVER reaches the user. Unknown, expired, spent and claimed-by-someone-
    else are one message on purpose: telling them apart would confirm to somebody guessing codes
    that a given code exists. If you are reading this because it seems unhelpful to know the reason
    and not say it — that is the point, and the vagueness is the thing being protected.

    It runs strictly after the transition has already failed, so it cannot influence the write, and
    its result is only ever attached to a metric and a log line. Without it every bug on this path
    would degrade into the same friendly message with nothing to alert on, because a conditional
    UPDATE that matches zero rows looks exactly like an expired code.
    """
    failure = await pending_links.classify_claim_failure(session, code, tg_user_id) if code else None
    # The stage rides along with the cause, because the same cause means different things on either
    # side: a row claimed by another account seen at `claim` is a forwarded link, seen at `consume`
    # it is a forged confirm button. Both are near-zero-baseline and therefore alarmable, unlike an
    # expired code, which has an honest rate.
    detail = f"{stage}:{failure.name.lower() if failure is not None else 'no_code'}"
    refuse(context, RedemptionRefusal.CODE_NOT_USABLE, detail)
    log.info(
        "Patreon pairing code could not be used",
        flow=patreon_link.LINK_FLOW,
        stage=stage,
        outcome=RedemptionRefusal.CODE_NOT_USABLE.name.lower(),
        reason=detail,
        # Enough to correlate a support question with a row without storing anything reversible.
        code_hash_prefix=pairing.hash_pairing_code(code)[:8] if code else None,
        tg_user_id=tg_user_id,
    )


def language_for(update: Update, user: User | None) -> str:
    """The language to answer a redemption in, for a sender who may have no account yet."""
    if user is not None:
        return user.lang
    tg_user = update.effective_user
    return locale_for_language_code(tg_user.language_code if tg_user is not None else None)


@HandlersRegistry.register_command(
    CollaborateHandlerId.PATREON_LINK_REDEEM,
    command="start",
    group=PATREON_PAIRING_HANDLERS_GROUP,
    # Private chats only: the deep link always opens the user's own chat with the bot, so the only
    # way a pairing command reaches a group is somebody pasting it there — and answering it in the
    # group would show the confirmation prompt (and the Patreon name on it) to everyone present,
    # with its buttons tappable by any member.
    filters=PatreonPairingStartFilter() & filters.ChatType.PRIVATE,
)
@claim_update
@with_session
async def command_start_patreon_link(session: AsyncSession, update: Update, context: TMitupContext) -> None:
    """Claim a pairing code and open the confirmation prompt.

    Nothing is written to the subscription here, and the pending row is only bound to the sender —
    never spent. Every refusal leaves the row untouched, so a link that arrives too early can simply
    be tapped again once the reason is fixed.
    """
    if update.effective_user is None:
        raise EffectiveUserNotSet(update)
    payload = parse_start_payload(context.args)
    code = payload.token if payload is not None else None
    tg_user_id = update.effective_user.id
    user = await User.by_tg_user_id(session, tg_user_id, load_collections=False)
    lang = language_for(update, user)

    if user is not None and user.status is UserStatus.DELETION_REQUESTED:
        # The account is mid-deletion, so there is nothing to link to and nothing to set up.
        await context.api.send_message(update=update, view=PrivacyMessages.PENDING_DELETION_ALERT.get(lang=lang))
        refuse(context, RedemptionRefusal.PENDING_DELETION)
        return

    if user is None or user.status is not UserStatus.MEMBER:
        await context.api.send_message(update=update, view=patreon_link_needs_setup_view(lang))
        refuse(context, RedemptionRefusal.NOT_A_MEMBER)
        log.info("Patreon pairing code presented before setup", flow=patreon_link.LINK_FLOW, stage="claim")
        return

    claimed = await pending_links.claim_pending_link(session, code, tg_user_id) if code else None
    if claimed is None:
        await context.api.send_message(update=update, view=patreon_link_code_not_valid_view(lang))
        await refuse_unusable_code(session, context, code, tg_user_id, stage="claim")
        return

    assert code is not None, "a claimed link means the code was present"
    await context.api.send_message(
        update=update,
        view=patreon_link_confirmation_view(
            guards.render_context(user, update, context),
            patreon_name=display_name(claimed),
            code=code,
            # Read live, and used only to pick and fill the warning: what the confirmation would
            # grant is on the pending row and is re-read from it at confirm time.
            current_level=user.supporter_level,
        ),
    )
    context.put_feature_metric(Feature.PATREON_LINK, name=MetricKey.PATREON_LINK_PROMPTED)
    log.info(
        "Patreon link confirmation prompted",
        flow=patreon_link.LINK_FLOW,
        stage="prompt",
        tg_user_id=tg_user_id,
        patreon_user_id=claimed.patreon_user_id,
        granted_level=claimed.granted_level.value,
        current_level=user.supporter_level.value,
    )


def display_name(claimed: pending_links.ClaimedLink) -> str:
    """How to name the Patreon account in the prompt.

    Patreon omits ``full_name`` for accounts that never set one. Falling back to the numeric id is
    poor but honest: it is still something the reader can compare, and inventing a friendlier
    placeholder would make an unrecognisable account look ordinary.
    """
    return claimed.patreon_full_name or claimed.patreon_user_id


@HandlersRegistry.register_callback_query(
    CollaborateHandlerId.PATREON_LINK_CONFIRM, callback_data=cb.CONFIRM_PATREON_LINK, bindable=True
)
@with_session(write=True)
async def callback_query_confirm_patreon_link(session: AsyncSession, update: Update, context: TMitupContext) -> None:
    """Spend the pending link and write the subscription, for the account that claimed it.

    The code arrives in the callback data, but holding it is not enough: the consume statement also
    requires the row to have been claimed by the confirming account, so a forged button naming
    somebody else's pending link matches nothing and writes nothing.

    An account marked for deletion between the prompt and the tap is rejected by
    ``guards.current_user`` before anything here runs, which leaves the pending row unspent.
    """
    valid = guards.valid_code_callback_data(
        cb.CONFIRM_PATREON_LINK.parse(context.match), CollaborateHandlerId.PATREON_LINK_CONFIRM
    )
    user = await guards.current_user(update, session)

    confirmed = await pending_links.consume_pending_link(session, valid.code, user.tg_user_id)
    if confirmed is None:
        # Routine, not exceptional: a double tap, a back button, or a prompt left open past its
        # expiry all land here, as does a confirm aimed at a row this account never claimed.
        await answer_failed_consume(session, update, context, user, valid.code)
        return

    outcome = await patreon_link.link_patreon_account(
        session,
        context.api,
        user,
        patreon_user_id=confirmed.patreon_user_id,
        # Straight from the row the consume statement returned. Never from the callback data, and
        # never from anything the prompt rendered.
        granted_level=confirmed.granted_level,
    )
    await answer_link_outcome(session, update, context, user, outcome)


async def answer_failed_consume(
    session: AsyncSession, update: Update, context: TMitupContext, user: User, code: str
) -> None:
    """Answer a confirm whose consume matched no row.

    One shape gets a truthful exception to the vague answer: when this same account already spent
    this exact code and holds the link it wrote, the failed consume is a double tap losing to its
    own first tap, and "no longer works — nothing changed" would be false on both counts. They see
    the linked screen the winning tap produced, and nothing is metered: the completed flow was
    already counted once. Every other shape stays deliberately vague — see ``refuse_unusable_code``.
    """
    spent = await pending_links.spent_link_of(session, code, user.tg_user_id)
    subscription = await subscription_for_user(session, user) if spent is not None else None
    if spent is not None and subscription is not None and subscription.patreon_user_id == spent.patreon_user_id:
        await context.api.edit_message(update=update, view=await build_collaborate_view(session, user, context))
        log.info(
            "Patreon link confirm repeated after success",
            flow=patreon_link.LINK_FLOW,
            stage="consume",
            outcome="already_linked_by_this_code",
            tg_user_id=user.tg_user_id,
        )
        return
    await context.api.edit_message(update=update, view=patreon_link_code_not_valid_view(user.lang))
    await refuse_unusable_code(session, context, code, user.tg_user_id, stage="consume")


async def answer_link_outcome(
    session: AsyncSession, update: Update, context: TMitupContext, user: User, outcome: LinkOutcome
) -> None:
    """Render what confirming produced, exhaustively — every outcome names its own answer, so a new
    LinkOutcome member cannot silently ride the success branch."""
    match outcome:
        case LinkOutcome.ALREADY_LINKED_ELSEWHERE:
            await context.api.edit_message(update=update, view=patreon_already_linked_elsewhere_view(user.lang))
            refuse(context, RedemptionRefusal.ALREADY_LINKED_ELSEWHERE)
        case LinkOutcome.PENDING_DELETION:
            # Backstop: guards.current_user rejects a deletion-requested account before the consume
            # runs, so an honest tap never reaches this arm. If it ever runs, the code is spent and
            # nothing was written — the answer is the same alert every surface shows a dying
            # account, never a screen implying a link happened.
            await context.api.edit_message(
                update=update, view=PrivacyMessages.PENDING_DELETION_ALERT.get(lang=user.lang)
            )
            refuse(context, RedemptionRefusal.PENDING_DELETION)
        case LinkOutcome.LINKED_SUPPORTER | LinkOutcome.LINKED_NO_PATRON:
            # The subscription row is still pending in the session; the view's own query autoflushes
            # it, so the screen renders the linked state rather than the not-linked one.
            await context.api.edit_message(update=update, view=await build_collaborate_view(session, user, context))
            context.put_feature_metric(Feature.PATREON_LINK, name=MetricKey.FLOW_COMPLETED)
        case _ as unreachable:
            assert_never(unreachable)


@HandlersRegistry.register_callback_query(
    CollaborateHandlerId.PATREON_LINK_DECLINE, callback_data=cb.DECLINE_PATREON_LINK, bindable=True
)
@with_session
async def callback_query_decline_patreon_link(session: AsyncSession, update: Update, context: TMitupContext) -> None:
    """Back out of the prompt without writing anything.

    The binding burns the code, not this handler. From the moment an account claims a row that code
    is theirs alone, whatever they do next — decline, ignore it, close Telegram — and nothing
    releases it before it expires. Declining just does not undo that.

    Freeing the row here would look tidy and would be a real weakening: a forwarded code could then
    be retried against a second person, and a third, so one consent would become a reusable attack
    instead of a single-target one. The person who declined is not stranded, because the claim
    predicate admits the same sender again — re-tapping their own link re-opens this prompt.
    """
    user = await guards.current_user(update, session)
    await context.api.edit_message(update=update, view=patreon_link_declined_view(user.lang))
    context.put_feature_metric(Feature.PATREON_LINK, name=MetricKey.FEATURE_CANCELLED)
    log.info("Patreon link confirmation declined", flow=patreon_link.LINK_FLOW, stage="prompt", outcome="declined")
