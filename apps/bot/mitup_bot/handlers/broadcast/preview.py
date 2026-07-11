from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot.db import with_session
from mitup_bot.keyboards import ButtonConfig, Keyboard
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import Broadcast, BroadcastMessage, User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.entities import FormattedText
from mitup_bot.utils.messages import BroadcastOperatorMessages
from mitup_bot.views import MitupView, factory

from . import utils
from .enums import ConversationBroadcastState
from .validation import ValidatedBroadcast


async def present_preview(
    update: Update, context: TMitupContext, operator: User, validated: ValidatedBroadcast
) -> ConversationBroadcastState:
    """Render the previews, persist the draft, and send the confirm/cancel summary.

    No DB session spans the Telegram sends: the previews go out first with no session open, then
    the recipient reads and the draft creation each run in their own short transaction.
    """
    await render_language_previews(context, operator, validated)
    recipient_counts = await compute_recipient_counts(validated)
    draft_id = await create_draft(operator, validated)
    summary = summary_text(operator.lang, validated, recipient_counts)
    keyboard = confirmation_keyboard(operator.lang, draft_id)
    await context.api.send_message(update=update, view=MitupView(summary, keyboard))
    return ConversationBroadcastState.AWAITING_CONTENT


async def render_language_previews(context: TMitupContext, operator: User, validated: ValidatedBroadcast):
    """Show a header, then for each language a bold label followed by the exact recipient preview.

    Each preview is its own message built with `factory.broadcast_recipient_view` — the same view
    the sender delivers — so the preview equals the actual send exactly.
    """
    await context.api.send_message_to_user(operator, BroadcastOperatorMessages.PREVIEW_HEADER.get(lang=operator.lang))
    for content in validated.messages:
        await context.api.send_message_to_user(operator, language_label(operator.lang, content.language))
        await context.api.send_message_to_user(
            operator, factory.broadcast_recipient_view(content.body_html, content.language)
        )


def language_label(lang: str, code: str) -> FormattedText:
    display_name = utils.LANGUAGE_NAMES.get(code)
    display = display_name.get(lang=lang) if display_name is not None else code
    return BroadcastOperatorMessages.PREVIEW_LANGUAGE_LABEL.get(lang=lang, language=display)


@with_session
async def compute_recipient_counts(session: AsyncSession, validated: ValidatedBroadcast) -> dict[str, int]:
    members_by_language = await utils.count_members_by_language(session)
    return utils.recipients_per_language(members_by_language, [content.language for content in validated.messages])


@with_session
async def create_draft(session: AsyncSession, operator: User, validated: ValidatedBroadcast) -> int:
    await utils.discard_author_drafts(session, operator.tg_user_id)
    broadcast = Broadcast(
        name=utils.derive_name(validated.english_body),
        author_tg_id=operator.tg_user_id,
        messages=[
            BroadcastMessage(language=content.language, body_html=content.body_html) for content in validated.messages
        ],
    )
    session.add(broadcast)
    await session.flush()
    return broadcast.db_id


def summary_text(lang: str, validated: ValidatedBroadcast, recipient_counts: dict[str, int]) -> FormattedText:
    parts = [BroadcastOperatorMessages.PREVIEW_SUMMARY_HEADER.get(lang=lang)]
    parts.extend(
        BroadcastOperatorMessages.PREVIEW_SUMMARY_LINE.get(
            lang=lang,
            language=content.language,
            char_count=content.char_count,
            recipient_count=recipient_counts.get(content.language, 0),
        )
        for content in validated.messages
    )
    parts.append(
        BroadcastOperatorMessages.PREVIEW_TOTAL_RECIPIENTS.get(lang=lang, total=sum(recipient_counts.values()))
    )
    if validated.skipped_languages:
        parts.append(BroadcastOperatorMessages.PREVIEW_WARNINGS_HEADER.get(lang=lang))
        parts.extend(
            BroadcastOperatorMessages.PREVIEW_WARNING_LINE.get(lang=lang, language=language)
            for language in validated.skipped_languages
        )
    parts.append(BroadcastOperatorMessages.PREVIEW_FOOTER.get(lang=lang))
    return FormattedText.join("\n\n", parts)


def confirmation_keyboard(lang: str, broadcast_id: int) -> Keyboard:
    return [
        [
            ButtonConfig(
                text=BroadcastOperatorMessages.BUTTON_CONFIRM.get_text(lang=lang),
                callback_data=cb.CONFIRM_BROADCAST.with_id(broadcast_id),
            ),
            ButtonConfig(
                text=BroadcastOperatorMessages.BUTTON_CANCEL.get_text(lang=lang),
                callback_data=cb.CANCEL_BROADCAST.with_id(broadcast_id),
            ),
        ]
    ]
