import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models.users import UserStatus
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, PrivacyMessages
from mitup_bot.views import MitupView, ViewDocument, factory

from . import data_export
from .enums import PrivacyHandlerId

log = structlog.get_logger(__name__)


@HandlersRegistry.register_callback_query(PrivacyHandlerId.SHOW, callback_data=cb.EDIT_PRIVACY)
@with_session
async def callback_query_show_privacy(session: AsyncSession, update: Update, context: TMitupContext):
    # Privacy screens read only `user.lang`, never the meetups/joined_links collections.
    user = await guards.current_user(update, session)
    await context.api.edit_message(
        update=update, view=factory.privacy_view(guards.render_context(user, update, context))
    )


@HandlersRegistry.register_callback_query(PrivacyHandlerId.EXPORT_DATA, callback_data=cb.EXPORT_USER_DATA)
@with_session
async def callback_query_export_user_data(session: AsyncSession, update: Update, context: TMitupContext):
    # The export builder loads every traversal itself; only the user's own columns are read here.
    user = await guards.current_user(update, session)
    export = await data_export.build_user_export(session, user)
    document, filename = data_export.export_document(export)
    # The document arrives as a new message, so the privacy screen above keeps its buttons.
    # Its own button sends a fresh privacy screen so the document is not a dead end. Plain label
    # (no « decoration) since this is navigation from a standalone message, not a back button.
    view = MitupView(
        description=PrivacyMessages.EXPORT_CAPTION.get(lang=user.lang),
        keyboard=[[ButtonConfig(text=ButtonMessages.PRIVACY.get_text(lang=user.lang), callback_data=cb.SEND_PRIVACY)]],
        document=ViewDocument(content=document, filename=filename),
    )
    await context.api.send_document(update=update, view=view)
    # Privacy events are far too sparse for a useful CloudWatch series; the searchable log
    # carries the same information.
    log.info("User data export sent", user_id=user.db_id)


@HandlersRegistry.register_callback_query(PrivacyHandlerId.SEND_PRIVACY, callback_data=cb.SEND_PRIVACY, bindable=True)
@with_session
async def callback_query_send_privacy(session: AsyncSession, update: Update, context: TMitupContext):
    # Navigation-only entry point for standalone messages (the export document): send the privacy
    # screen as a NEW message so the tapped message stays in the chat with its button intact.
    user = await guards.current_user(update, session)
    await context.api.send_message(
        update=update, view=factory.privacy_view(guards.render_context(user, update, context))
    )


@HandlersRegistry.register_callback_query(PrivacyHandlerId.DELETE_DATA, callback_data=cb.DELETE_USER_DATA)
@with_session
async def callback_query_delete_user_data(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session)
    view = factory.confirmation_view(
        guards.render_context(user, update, context),
        message=PrivacyMessages.DELETE_WARNING.get(lang=user.lang),
        confirm_callback_data=cb.CONFIRM_DELETE_USER_DATA,
        decline_callback_data=cb.DECLINE_DELETE_USER_DATA,
    )
    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    PrivacyHandlerId.CONFIRM_DELETE_DATA, callback_data=cb.CONFIRM_DELETE_USER_DATA
)
@with_session
async def callback_query_confirm_delete_user_data(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session)
    view = factory.confirmation_view(
        guards.render_context(user, update, context),
        message=PrivacyMessages.DELETE_LAST_CHANCE.get(lang=user.lang),
        confirm_callback_data=cb.CONFIRM_DELETE_USER_DATA_FINAL,
        decline_callback_data=cb.DECLINE_DELETE_USER_DATA,
    )
    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    PrivacyHandlerId.CONFIRM_DELETE_DATA_FINAL, callback_data=cb.CONFIRM_DELETE_USER_DATA_FINAL
)
@with_session
async def callback_query_confirm_delete_user_data_final(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session)
    user.set_status(UserStatus.DELETION_REQUESTED)
    log.info("Data deletion request confirmed", user_id=user.db_id)
    # No buttons: the account has stopped working, so there is no screen left to navigate to.
    view = MitupView(description=PrivacyMessages.DELETION_MARKED.get(lang=user.lang), keyboard=[])
    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    PrivacyHandlerId.DECLINE_DELETE_DATA, callback_data=cb.DECLINE_DELETE_USER_DATA
)
@with_session
async def callback_query_decline_delete_user_data(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session)
    await context.api.edit_message(
        update=update, view=factory.privacy_view(guards.render_context(user, update, context))
    )
