"""Reporting phase: resolve the operator recipients and render the one-time summary DM. See the
package docstring in `__init__.py` for the once-only notification guarantee."""

import structlog
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import db
from mitup_bot.api_wrapper import TelegramApiWrapper
from mitup_bot.exceptions import InactiveUserInteraction
from mitup_bot.models import User
from mitup_bot.models.broadcasts import BroadcastStatus
from mitup_bot.utils.entities import FormattedText
from mitup_bot.utils.messages import BroadcastOperatorMessages
from mitup_bot.views import MitupView

from .types import BroadcastSummary, LanguageBreakdown

log = structlog.get_logger(__name__)


async def notify_operators(api: TelegramApiWrapper, admin_tg_ids: list[int], summary: BroadcastSummary):
    operators = await load_operators(admin_tg_ids)
    for tg_id, operator in operators.items():
        view = build_summary_view(summary, operator.lang)
        try:
            await api.send_message_to_user(operator, view)
        except InactiveUserInteraction:
            log.warning("Broadcast operator unreachable for summary", tg_user_id=tg_id)


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
