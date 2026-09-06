import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards
from mitup_bot.db import with_session
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.handlers.utils import Screen, ScreenDelivery, log_screen_shown
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.models.users import UserStatus
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import PrivacyMessages
from mitup_bot.utils.rich_message import RichDocument
from mitup_bot.views import MitupView, factory

from . import data_export
from .enums import PrivacyHandlerId

log = structlog.get_logger(__name__)


@HandlersRegistry.register_callback_query(PrivacyHandlerId.SHOW, callback_data=cb.EDIT_PRIVACY)
@with_session
async def callback_query_show_privacy(session: AsyncSession, update: Update, context: TMitupContext):
    # Privacy screens read only `user.lang`, never the meetups/joined_links collections.
    user = await guards.current_user(update, session)
    log_screen_shown(user, Screen.PRIVACY, ScreenDelivery.EDIT)
    await context.api.edit_message(
        update=update, view=factory.privacy_view(guards.render_context(user, update, context))
    )


@HandlersRegistry.register_callback_query(PrivacyHandlerId.EXPORT_DATA, callback_data=cb.EXPORT_USER_DATA)
@with_session
async def callback_query_export_user_data(session: AsyncSession, update: Update, context: TMitupContext):
    # The export builder loads every traversal itself; only the user's own columns are read here.
    user = await guards.current_user(update, session)
    log.info("User data export requested", user_id=user.db_id)

    export = await data_export.build_user_export(session, user)
    document, filename = data_export.export_document(export)
    await context.api.edit_message(
        update=update,
        view=factory.privacy_view(
            guards.render_context(user, update, context),
            export=RichDocument(content=document, filename=filename),
        ),
    )
    # Privacy events are far too sparse for a useful CloudWatch series; the searchable log
    # carries the same information. An Art. 20 disclosure has to evidence its own scope, so the
    # line names what left the system rather than only that something did.
    log.info(
        "User data export sent",
        user_id=user.db_id,
        **data_export.export_scope(export),
        document_bytes=len(document),
        # Not `filename`: the stdlib `LogRecord` owns that attribute and refuses to have it
        # overwritten, which raises inside the logging call rather than dropping the field.
        export_filename=filename,
    )


@HandlersRegistry.register_callback_query(PrivacyHandlerId.DELETE_DATA, callback_data=cb.DELETE_USER_DATA)
@with_session
async def callback_query_delete_user_data(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session)
    log.info("Data deletion step shown", user_id=user.db_id, step="warning")
    view = factory.confirmation_view(
        guards.render_context(user, update, context),
        message=PrivacyMessages.DELETE_WARNING.rich(lang=user.lang),
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
    log.info("Data deletion step shown", user_id=user.db_id, step="last_chance")
    view = factory.confirmation_view(
        guards.render_context(user, update, context),
        message=PrivacyMessages.DELETE_LAST_CHANCE.rich(lang=user.lang),
        confirm_callback_data=cb.CONFIRM_DELETE_USER_DATA_FINAL,
        decline_callback_data=cb.DECLINE_DELETE_USER_DATA,
    )
    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    PrivacyHandlerId.CONFIRM_DELETE_DATA_FINAL, callback_data=cb.CONFIRM_DELETE_USER_DATA_FINAL
)
@with_session
async def callback_query_confirm_delete_user_data_final(session: AsyncSession, update: Update, context: TMitupContext):
    # load_collections: the line below is the only surviving record of what the cleanup run will
    # delete, so it counts the rows while they still exist.
    user = await guards.current_user(update, session, load_collections=True)
    has_patreon_link = (await session.exec(data_export.subscription_statement(user))).first() is not None

    prior_status = user.status
    user.set_status(UserStatus.DELETION_REQUESTED)
    log.info(
        "Data deletion requested",
        user_id=user.db_id,
        prior_status=prior_status.value,
        owned_meetings=len(user.meetups),
        joined_meetings=len(user.joined_links),
        has_patreon_link=has_patreon_link,
        reason="user_confirmed_final",
    )
    # No buttons: the account has stopped working, so there is no screen left to navigate to.
    view = MitupView(message=PrivacyMessages.DELETION_MARKED.rich(lang=user.lang), menu=[])
    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(
    PrivacyHandlerId.DECLINE_DELETE_DATA, callback_data=cb.DECLINE_DELETE_USER_DATA
)
@with_session
async def callback_query_decline_delete_user_data(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session)
    log.info("Data deletion declined", user_id=user.db_id, status=user.status.value, reason="user_declined")
    await context.api.edit_message(
        update=update, view=factory.privacy_view(guards.render_context(user, update, context))
    )
