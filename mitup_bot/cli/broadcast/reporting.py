"""Reporting phase: resolve the operator recipients and render the one-time summary DM. See the
package docstring in `__init__.py` for the once-only notification guarantee."""

import structlog
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import db
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.models import User
from mitup_bot.models.broadcasts import BroadcastStatus
from mitup_bot.utils.entities import FormattedText
from mitup_bot.utils.messages import BroadcastOperatorMessages
from mitup_bot.views import MitupView

from .types import BroadcastSummary, LanguageBreakdown

log = structlog.get_logger(__name__)


async def notify_operators(
    api: TelegramApiWrapper, admin_tg_ids: list[int], author_tg_id: int, summary: BroadcastSummary
):
    """DM the finalization summary to the broadcast's author, falling back to every admin.

    The author gets the summary because they are the one who ran the broadcast. If the author has
    no `User` row (never DM-ed the bot) or is unreachable, we fall back to DMing all admins —
    minus the author id we already failed on, since they are normally an admin too.
    """
    if await notify_author(api, author_tg_id, summary):
        return
    fallback_ids = [tg_id for tg_id in admin_tg_ids if tg_id != author_tg_id]
    await notify_admins(api, fallback_ids, summary)


async def notify_author(api: TelegramApiWrapper, author_tg_id: int, summary: BroadcastSummary) -> bool:
    """Try to DM the author the summary. Returns whether they received it — a missing `User` row or
    ANY send failure returns `False` so the caller falls back to the admins.

    The broad `except Exception` is deliberate: this runs AFTER the terminal compare-and-swap, so
    the broadcast is already DONE/FAILED and will never be re-claimed. If any error (a `TimedOut`,
    an exhausted-retries `RetryAfter`, a stray `BadRequest`, even a `NetworkError`) escaped here,
    the run would fault with the DM lost forever and the admin fallback never reached — so we
    swallow it, log, and fall back rather than re-raise.
    """
    author = await resolve_author(author_tg_id)
    if author is None:
        log.warning("Broadcast author has no reachable user, falling back to admins", tg_user_id=author_tg_id)
        return False
    view = build_summary_view(summary, author.lang)
    try:
        await api.send_message_to_user(author, view)
    except Exception as error:
        log.warning(
            "Broadcast author unreachable for summary, falling back to admins",
            tg_user_id=author_tg_id,
            error=str(error),
        )
        return False
    return True


@db.with_session
async def resolve_author(session: AsyncSession, author_tg_id: int) -> User | None:
    return await User.by_tg_user_id(session, author_tg_id)


async def notify_admins(api: TelegramApiWrapper, admin_tg_ids: list[int], summary: BroadcastSummary):
    """DM the summary to every admin. A broad `except Exception` per operator (same terminal-path
    reasoning as `notify_author`) so one unreachable or erroring admin never kills the DM to the
    rest, and never faults an already-finalized run."""
    operators = await load_operators(admin_tg_ids)
    for tg_id, operator in operators.items():
        view = build_summary_view(summary, operator.lang)
        try:
            await api.send_message_to_user(operator, view)
        except Exception as error:
            log.warning("Broadcast operator unreachable for summary", tg_user_id=tg_id, error=str(error))


@db.with_session
async def load_operators(session: AsyncSession, admin_tg_ids: list[int]) -> dict[int, User]:
    operators: dict[int, User] = {}
    for tg_id in admin_tg_ids:
        if operator := await User.by_tg_user_id(session, tg_id):
            operators[tg_id] = operator
            continue
        log.warning("Broadcast operator has no reachable user, skipping summary", tg_user_id=tg_id)
    return operators


def build_summary_view(summary: BroadcastSummary, lang: str) -> MitupView:
    if summary.status is BroadcastStatus.FAILED:
        text = BroadcastOperatorMessages.SENDER_FAILED.get(
            lang=lang,
            broadcast_id=summary.broadcast_id,
            name=summary.name,
            attempts=summary.attempts,
            sent=summary.sent,
            failed=summary.failed,
            skipped=summary.skipped,
        )
        if summary.orphaned:
            text = FormattedText.join(
                "\n\n",
                [text, BroadcastOperatorMessages.SENDER_ORPHANED_WARNING.get(lang=lang, orphaned=summary.orphaned)],
            )
        return MitupView(text, keyboard=[])

    breakdown = FormattedText.join(
        "\n",
        [build_breakdown_line(line, lang) for line in summary.breakdown],
    )
    text = BroadcastOperatorMessages.SENDER_COMPLETE_SUMMARY.get(
        lang=lang,
        broadcast_id=summary.broadcast_id,
        name=summary.name,
        total=summary.total,
        sent=summary.sent,
        failed=summary.failed,
        skipped=summary.skipped,
        breakdown=breakdown,
    )
    if summary.orphaned:
        text = FormattedText.join(
            "\n\n",
            [text, BroadcastOperatorMessages.SENDER_ORPHANED_WARNING.get(lang=lang, orphaned=summary.orphaned)],
        )
    return MitupView(text, keyboard=[])


def build_breakdown_line(line: LanguageBreakdown, lang: str) -> FormattedText:
    if line.orphaned:
        return BroadcastOperatorMessages.SENDER_BREAKDOWN_LINE_WITH_ORPHANED.get(
            lang=lang,
            language=line.language,
            sent=line.sent,
            failed=line.failed,
            skipped=line.skipped,
            orphaned=line.orphaned,
        )
    return BroadcastOperatorMessages.SENDER_BREAKDOWN_LINE.get(
        lang=lang, language=line.language, sent=line.sent, failed=line.failed, skipped=line.skipped
    )
