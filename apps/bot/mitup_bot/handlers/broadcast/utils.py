import structlog
from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.models import Broadcast, Settings, User
from mitup_bot.models.broadcasts import BroadcastStatus
from mitup_bot.models.users import UserStatus
from mitup_bot.translations import TranslationEngine
from mitup_bot.utils.messages import Languages

from .validation import strip_html

log = structlog.get_logger(__name__)

# Every DELETE of an operator's draft, from any of the three causes, under one name — the row and
# its messages are gone afterwards, so the line is the only record that the work existed.
DRAFT_DISCARDED_EVENT = "Broadcast draft discarded"

BROADCAST_NAME_MAX_LENGTH = 40

# Maps each SUPPORTED_LANGUAGES code to its display name (with flag) for the labelled previews.
LANGUAGE_NAMES: dict[str, Languages] = {
    "en": Languages.ENGLISH,
    "es_ES": Languages.SPANISH,
    "gl_ES": Languages.GALICIAN,
    "de_DE": Languages.GERMAN,
    "pt_BR": Languages.PORTUGUESE,
    "it_IT": Languages.ITALIAN,
}


async def count_members_by_language(session: AsyncSession) -> dict[str, int]:
    """Count reachable MEMBER users grouped by their settings language."""
    statement = (
        select(Settings.language, func.count())
        .join(User, onclause=col(User.id) == col(Settings.user_id))
        .where(col(User.status) == UserStatus.MEMBER)
        .group_by(col(Settings.language))
    )
    rows = await session.exec(statement)
    return {language: count for language, count in rows}


def folded_into_fallback(members_by_language: dict[str, int], provided_languages: list[str]) -> int:
    """How many members read the broadcast in English because their own language was not provided."""
    return sum(count for language, count in members_by_language.items() if language not in provided_languages)


def recipients_per_language(members_by_language: dict[str, int], provided_languages: list[str]) -> dict[str, int]:
    """Map each provided language to its recipient count, folding unprovided languages into English.

    English is always present (validation guarantees it), so it is the fallback bucket for every
    member whose language is not among the provided ones.
    """
    counts = {language: members_by_language.get(language, 0) for language in provided_languages}
    fallback = TranslationEngine.FALLBACK_LANG
    counts[fallback] = counts.get(fallback, 0) + folded_into_fallback(members_by_language, provided_languages)
    return counts


def derive_name(english_body: str) -> str:
    collapsed = " ".join(strip_html(english_body).split())
    return collapsed[:BROADCAST_NAME_MAX_LENGTH] or "Broadcast"


async def discard_author_drafts(session: AsyncSession, author_tg_id: int, *, reason: str):
    """Delete any DRAFT broadcasts left by this operator so at most one draft exists per author.

    `reason` is required: the same sweep runs when the flow is re-entered and when a new upload
    replaces an earlier one, and a deleted draft is unrecoverable either way.
    """
    statement = select(Broadcast).where(
        col(Broadcast.author_tg_id) == author_tg_id, col(Broadcast.status) == BroadcastStatus.DRAFT
    )
    drafts = await session.exec(statement)
    for draft in drafts:
        log_draft_discarded(draft, reason=reason)
        await session.delete(draft)


def log_draft_discarded(draft: Broadcast, *, reason: str):
    log.info(
        DRAFT_DISCARDED_EVENT,
        broadcast_id=draft.db_id,
        broadcast_name=draft.name,
        author_tg_id=draft.author_tg_id,
        stage="discard_draft",
        reason=reason,
    )
