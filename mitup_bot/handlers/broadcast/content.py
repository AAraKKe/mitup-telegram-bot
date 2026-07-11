from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Document, Message, Update
from telegram.ext import filters

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models import User
from mitup_bot.utils.messages import BroadcastOperatorMessages

from .enums import BroadcastHandlerId, ConversationBroadcastState
from .preview import present_preview
from .validation import BroadcastContentError, parse_and_validate

MAX_DOCUMENT_BYTES = 256 * 1024

CONTENT_FILTER = filters.Document.ALL | (filters.TEXT & ~filters.COMMAND)


# Deliberately undecorated: the document download and the per-language preview sends must not run
# inside an open DB session. The operator load and the draft/recipient reads each take their own
# short transaction (see load_operator and preview.py), so no connection is held across Telegram I/O.
@HandlersRegistry.register_message(
    BroadcastHandlerId.BROADCAST_CONTENT_MESSAGE, CONTENT_FILTER, bindable=False, admin_only=True
)
async def broadcast_content_message_handler(update: Update, context: TMitupContext) -> ConversationBroadcastState:
    operator = await load_operator(update)
    if operator is None:
        return ConversationBroadcastState.AWAITING_CONTENT
    message = guards.message(update)

    raw = await read_raw_content(update, context, message, operator)
    if raw is None:
        return ConversationBroadcastState.AWAITING_CONTENT

    try:
        validated = parse_and_validate(raw)
    except BroadcastContentError as error:
        await context.api.send_message(update=update, view=error.message.get(lang=operator.lang, **error.params))
        return ConversationBroadcastState.AWAITING_CONTENT

    return await present_preview(update, context, operator, validated)


@HandlersRegistry.register_message(
    BroadcastHandlerId.BROADCAST_INVALID_CONTENT_MESSAGE, ~filters.COMMAND, bindable=False, admin_only=True
)
async def broadcast_invalid_content_message_handler(
    update: Update, context: TMitupContext
) -> ConversationBroadcastState:
    operator = await load_operator(update)
    if operator is None:
        return ConversationBroadcastState.AWAITING_CONTENT
    await context.api.send_message(update=update, view=BroadcastOperatorMessages.UPLOAD_PROMPT.get(lang=operator.lang))
    return ConversationBroadcastState.AWAITING_CONTENT


@with_session
async def load_operator(session: AsyncSession, update: Update) -> User | None:
    return await guards.member_user(update, session)


async def read_raw_content(update: Update, context: TMitupContext, message: Message, operator: User) -> str | None:
    if (document := message.document) is not None:
        return await read_document(update, context, document, operator)
    # text_html turns any formatting the operator applied (bold, italic, links) into the HTML tags
    # the YAML expects, so a pasted-and-styled message is honoured. Files stay raw (see read_document).
    return message.text_html


async def read_document(update: Update, context: TMitupContext, document: Document, operator: User) -> str | None:
    if document.file_size is not None and document.file_size > MAX_DOCUMENT_BYTES:
        await reject_document(update, context, operator)
        return None

    # Direct context.bot access is sanctioned here: the api-wrapper skill covers messaging calls,
    # but there is no wrapper path for file downloads, so get_file is the intended route.
    telegram_file = await context.bot.get_file(document.file_id)
    content = bytes(await telegram_file.download_as_bytearray())
    if len(content) > MAX_DOCUMENT_BYTES:
        await reject_document(update, context, operator)
        return None

    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        await context.api.send_message(
            update=update, view=BroadcastOperatorMessages.ERROR_DOCUMENT_DECODE.get(lang=operator.lang)
        )
        return None


async def reject_document(update: Update, context: TMitupContext, operator: User):
    await context.api.send_message(
        update=update,
        view=BroadcastOperatorMessages.ERROR_DOCUMENT_TOO_LARGE.get(
            lang=operator.lang, limit_kb=MAX_DOCUMENT_BYTES // 1024
        ),
    )
