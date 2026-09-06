from dataclasses import dataclass

import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.error import BadRequest

from mitup_bot.db import with_session
from mitup_bot.keyboards import ButtonConfig, Keyboard
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import Broadcast, BroadcastMessage, User
from mitup_bot.translations import TranslationEngine
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import BroadcastOperatorMessages
from mitup_bot.utils.rich_message import RichContent, RichMessageTooLong
from mitup_bot.views import MitupView, factory

from . import utils
from .enums import ConversationBroadcastState
from .validation import ValidatedBroadcast

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class RejectedPreview:
    """The language whose preview never went out, and what the send said about it."""

    language: str
    reason: str


async def present_preview(
    update: Update, context: TMitupContext, operator: User, validated: ValidatedBroadcast
) -> ConversationBroadcastState:
    """Render the previews, persist the draft, and send the confirm/cancel summary.

    The previews go out before anything is written, so a body Telegram refuses ends the submission
    with no draft behind it. No DB session spans the Telegram sends: the recipient reads and the
    draft creation each run in their own short transaction.
    """
    if (rejected := await render_language_previews(context, operator, validated)) is not None:
        await context.api.send_message(
            update=update,
            view=BroadcastOperatorMessages.ERROR_PREVIEW_REJECTED.rich(
                lang=operator.lang, language=rejected.language, reason=rejected.reason
            ),
        )
        return ConversationBroadcastState.AWAITING_CONTENT
    recipient_counts = await compute_recipient_counts(validated)
    draft_id = await create_draft(operator, validated)
    # From here on the draft has an id, and it is the key the sender's own run logs are bound to,
    # so one `broadcast_id` filter returns the operator's side and the delivery side together.
    with structlog.contextvars.bound_contextvars(broadcast_id=draft_id):
        summary = summary_text(operator.lang, validated, recipient_counts)
        keyboard = confirmation_keyboard(operator.lang, draft_id)
        await context.api.send_message(update=update, view=MitupView(summary, keyboard))
        log.info(
            "Broadcast confirmation requested",
            user_id=operator.db_id,
            stage="preview",
            outcome="awaiting_confirmation",
            total_recipients=sum(recipient_counts.values()),
            skipped_languages=validated.skipped_languages,
        )
    return ConversationBroadcastState.AWAITING_CONTENT


async def render_language_previews(
    context: TMitupContext, operator: User, validated: ValidatedBroadcast
) -> RejectedPreview | None:
    """Show a header, then for each language a bold label followed by the exact recipient preview.

    Each preview is its own message built with `factory.broadcast_recipient_view`, the same view the
    sender delivers, so a body that cannot be sent here is one no recipient could have received. The
    first refusal ends the run, and its reason quotes the operator's own message back, which is why
    it goes to them rather than to the logs.
    """
    await context.api.send_message_to_user(operator, BroadcastOperatorMessages.PREVIEW_HEADER.rich(lang=operator.lang))
    for content in validated.messages:
        await context.api.send_message_to_user(operator, language_label(operator.lang, content.language))
        try:
            await context.api.send_message_to_user(
                operator, factory.broadcast_recipient_view(content.body, content.language)
            )
        except (BadRequest, RichMessageTooLong) as error:
            log.warning(
                "Broadcast preview rejected",
                user_id=operator.db_id,
                stage="preview",
                outcome="rejected",
                language=content.language,
                reason="unsendable_body",
            )
            return RejectedPreview(content.language, str(error))
    log.info(
        "Broadcast previews sent to operator",
        user_id=operator.db_id,
        stage="preview",
        languages=[content.language for content in validated.messages],
        message_count=1 + 2 * len(validated.messages),
    )
    return None


def language_label(lang: str, code: str) -> RichContent:
    display_name = utils.LANGUAGE_NAMES.get(code)
    display = display_name.rich(lang=lang) if display_name is not None else code
    return BroadcastOperatorMessages.PREVIEW_LANGUAGE_LABEL.rich(lang=lang, language=display)


@with_session
async def compute_recipient_counts(session: AsyncSession, validated: ValidatedBroadcast) -> dict[str, int]:
    members_by_language = await utils.count_members_by_language(session)
    provided_languages = [content.language for content in validated.messages]
    counts = utils.recipients_per_language(members_by_language, provided_languages)
    # The audience is recomputed from a users table that keeps moving, so "why did N members get
    # English?" is unanswerable after the fact unless the counts behind the summary are recorded.
    log.info(
        "Broadcast audience computed",
        stage="audience",
        per_language=counts,
        total_recipients=sum(counts.values()),
        fallback_language=TranslationEngine.FALLBACK_LANG,
        folded_into_fallback=utils.folded_into_fallback(members_by_language, provided_languages),
        provided_languages=provided_languages,
    )
    return counts


@with_session
async def create_draft(session: AsyncSession, operator: User, validated: ValidatedBroadcast) -> int:
    await utils.discard_author_drafts(session, operator.tg_user_id, reason="replaced_by_new_upload")
    broadcast = Broadcast(
        name=utils.derive_name(validated.english_body),
        author_tg_id=operator.tg_user_id,
        messages=[
            BroadcastMessage(language=content.language, body_html=content.body) for content in validated.messages
        ],
    )
    session.add(broadcast)
    await session.flush()
    # Mints the id every later line — here, in the confirm handler and in the sender — references.
    log.info(
        "Broadcast draft created",
        broadcast_id=broadcast.db_id,
        broadcast_name=broadcast.name,
        author_tg_id=operator.tg_user_id,
        user_id=operator.db_id,
        stage="create_draft",
        languages=[content.language for content in validated.messages],
        char_counts={content.language: content.char_count for content in validated.messages},
        skipped_languages=validated.skipped_languages,
    )
    return broadcast.db_id


def summary_text(lang: str, validated: ValidatedBroadcast, recipient_counts: dict[str, int]) -> RichContent:
    parts = [BroadcastOperatorMessages.PREVIEW_SUMMARY_HEADER.rich(lang=lang)]
    parts.extend(
        BroadcastOperatorMessages.PREVIEW_SUMMARY_LINE.rich(
            lang=lang,
            language=content.language,
            char_count=content.char_count,
            recipient_count=recipient_counts.get(content.language, 0),
        )
        for content in validated.messages
    )
    parts.append(
        BroadcastOperatorMessages.PREVIEW_TOTAL_RECIPIENTS.rich(lang=lang, total=sum(recipient_counts.values()))
    )
    if validated.skipped_languages:
        parts.append(BroadcastOperatorMessages.PREVIEW_WARNINGS_HEADER.rich(lang=lang))
        parts.extend(
            BroadcastOperatorMessages.PREVIEW_WARNING_LINE.rich(lang=lang, language=language)
            for language in validated.skipped_languages
        )
    parts.append(BroadcastOperatorMessages.PREVIEW_FOOTER.rich(lang=lang))
    return RichContent.join("\n\n", parts)


def confirmation_keyboard(lang: str, broadcast_id: int) -> Keyboard:
    return [
        [
            ButtonConfig(
                text=BroadcastOperatorMessages.BUTTON_CONFIRM.text(lang=lang),
                callback_data=cb.CONFIRM_BROADCAST.with_id(broadcast_id),
                style="success",
            ),
            ButtonConfig(
                text=BroadcastOperatorMessages.BUTTON_CANCEL.text(lang=lang),
                callback_data=cb.CANCEL_BROADCAST.with_id(broadcast_id),
                style="danger",
            ),
        ]
    ]
