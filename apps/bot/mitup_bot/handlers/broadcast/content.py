import structlog
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

log = structlog.get_logger(__name__)

# An admin id with no MEMBER row reaches both upload handlers and is answered by neither, so both
# report the drop under one name.
UPLOAD_IGNORED_EVENT = "Broadcast content upload ignored"

# Every rejected document, whatever rejected it, with the reason naming which check failed.
DOCUMENT_REJECTED_EVENT = "Broadcast document rejected"

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
        log.warning(UPLOAD_IGNORED_EVENT, stage="load_operator", outcome="ignored", reason="operator_not_member_user")
        return ConversationBroadcastState.AWAITING_CONTENT
    message = guards.message(update)

    raw = await read_raw_content(update, context, message, operator)
    if raw is None:
        return ConversationBroadcastState.AWAITING_CONTENT

    try:
        validated = parse_and_validate(raw)
    except BroadcastContentError as error:
        # The reply is rendered from the same exception, so without this line the whole class of
        # upload failures leaves no trace at all — it never reaches the error handler.
        log.warning(
            "Broadcast content rejected",
            user_id=operator.db_id,
            stage="validate",
            outcome="rejected",
            reason=error.reason,
            **error.log_params,
        )
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
        log.warning(UPLOAD_IGNORED_EVENT, stage="load_operator", outcome="ignored", reason="operator_not_member_user")
        return ConversationBroadcastState.AWAITING_CONTENT
    await context.api.send_message(update=update, view=BroadcastOperatorMessages.UPLOAD_PROMPT.get(lang=operator.lang))
    log.info(
        "Broadcast upload prompt re-sent",
        user_id=operator.db_id,
        stage="receive",
        outcome="reprompted",
        reason="unsupported_message_type",
        message_kind=message_kind(update.effective_message),
    )
    return ConversationBroadcastState.AWAITING_CONTENT


def message_kind(message: Message | None) -> str:
    """Name what the operator sent instead of a broadcast body, as a bounded machine value.

    PTB resolves the attachment; a photo arrives as a tuple of sizes rather than as one object.
    """
    if message is None:
        return "unknown"
    attachment = message.effective_attachment
    if attachment is None:
        return "text" if message.text is not None else "empty"
    if isinstance(attachment, tuple):
        return "photo"
    return type(attachment).__name__.lower()


@with_session
async def load_operator(session: AsyncSession, update: Update) -> User | None:
    return await guards.member_user(update, session)


async def read_raw_content(update: Update, context: TMitupContext, message: Message, operator: User) -> str | None:
    if (document := message.document) is not None:
        return await read_document(update, context, document, operator)
    if message.text_html is None:
        # Answered by nothing: the operator gets no reply and the flow stays on the upload step.
        log.warning(
            "Broadcast content unreadable",
            user_id=operator.db_id,
            stage="receive",
            outcome="ignored",
            reason="empty_message_text",
        )
        return None
    # text_html turns any formatting the operator applied (bold, italic, links) into the HTML tags
    # the YAML expects, so a pasted-and-styled message is honoured. Files stay raw (see read_document).
    log.info(
        "Broadcast content received",
        user_id=operator.db_id,
        stage="receive",
        source="text",
        char_count=len(message.text_html),
    )
    return message.text_html


async def read_document(update: Update, context: TMitupContext, document: Document, operator: User) -> str | None:
    if document.file_size is not None and document.file_size > MAX_DOCUMENT_BYTES:
        await reject_document(update, context, document, operator, reason="declared_size_over_limit")
        return None

    # Direct context.bot access is sanctioned here: the api-wrapper skill covers messaging calls,
    # but there is no wrapper path for file downloads, so get_file is the intended route.
    telegram_file = await context.bot.get_file(document.file_id)
    content = bytes(await telegram_file.download_as_bytearray())
    if len(content) > MAX_DOCUMENT_BYTES:
        # A `file_size` header that under-reports the real download is the one case the first check
        # cannot catch, so the two rejections stay distinguishable.
        await reject_document(update, context, document, operator, reason="downloaded_size_over_limit")
        return None

    try:
        decoded = content.decode("utf-8")
    except UnicodeDecodeError:
        log_document_rejected(document, operator, reason="not_utf8", size_bytes=len(content))
        await context.api.send_message(
            update=update, view=BroadcastOperatorMessages.ERROR_DOCUMENT_DECODE.get(lang=operator.lang)
        )
        return None

    log.info(
        "Broadcast content received",
        user_id=operator.db_id,
        stage="receive",
        source="document",
        file_name=document.file_name,
        mime_type=document.mime_type,
        size_bytes=len(content),
    )
    return decoded


async def reject_document(update: Update, context: TMitupContext, document: Document, operator: User, *, reason: str):
    log_document_rejected(document, operator, reason=reason, size_bytes=document.file_size)
    await context.api.send_message(
        update=update,
        view=BroadcastOperatorMessages.ERROR_DOCUMENT_TOO_LARGE.get(
            lang=operator.lang, limit_kb=MAX_DOCUMENT_BYTES // 1024
        ),
    )


def log_document_rejected(document: Document, operator: User, *, reason: str, size_bytes: int | None):
    log.warning(
        DOCUMENT_REJECTED_EVENT,
        user_id=operator.db_id,
        stage="read_document",
        outcome="rejected",
        reason=reason,
        file_name=document.file_name,
        mime_type=document.mime_type,
        size_bytes=size_bytes,
        limit_bytes=MAX_DOCUMENT_BYTES,
    )
