from sqlalchemy import func
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.config import BotConfig
from mitup_bot.models import Broadcast, Settings, User
from mitup_bot.models.broadcasts import BroadcastStatus
from mitup_bot.models.users import UserStatus
from mitup_bot.translations import TranslationEngine
from mitup_bot.utils.messages import Languages
from mitup_bot.utils.mitup_types import TMitupContext

from .validation import strip_html

# Key under which the runtime stashes BotConfig in `application.bot_data` at startup (see
# MitupRuntime), so handlers can reach the broadcast allowlist without a module singleton.
BOT_CONFIG_KEY = "bot_config"

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


def bot_config(context: TMitupContext) -> BotConfig:
    config = context.bot_data.get(BOT_CONFIG_KEY)
    assert isinstance(config, BotConfig), "BotConfig must be stashed in bot_data at startup"
    return config


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


def recipients_per_language(members_by_language: dict[str, int], provided_languages: list[str]) -> dict[str, int]:
    """Map each provided language to its recipient count, folding unprovided languages into English.

    English is always present (validation guarantees it), so it is the fallback bucket for every
    member whose language is not among the provided ones.
    """
    counts = {language: members_by_language.get(language, 0) for language in provided_languages}
    fallback_extra = sum(count for language, count in members_by_language.items() if language not in provided_languages)
    counts[TranslationEngine.FALLBACK_LANG] = counts.get(TranslationEngine.FALLBACK_LANG, 0) + fallback_extra
    return counts


def derive_name(english_body: str) -> str:
    collapsed = " ".join(strip_html(english_body).split())
    return collapsed[:BROADCAST_NAME_MAX_LENGTH] or "Broadcast"


async def discard_author_drafts(session: AsyncSession, author_tg_id: int):
    """Delete any DRAFT broadcasts left by this operator so at most one draft exists per author."""
    statement = select(Broadcast).where(
        col(Broadcast.author_tg_id) == author_tg_id, col(Broadcast.status) == BroadcastStatus.DRAFT
    )
    drafts = await session.exec(statement)
    for draft in drafts:
        await session.delete(draft)
