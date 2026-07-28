import structlog
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update

from mitup_bot import guards, views
from mitup_bot.db import with_session
from mitup_bot.exceptions import InvalidLanguageError
from mitup_bot.handlers.registry import HandlersRegistry
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.translations import SUPPORTED_LANGUAGES
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import SettingsMessages

from .enums import EditSettingsHandlerId, SettingName
from .utils import SETTING_CHANGED_EVENT, SETTINGS_MENU_SOURCE

log = structlog.get_logger(__name__)


@HandlersRegistry.register_callback_query(EditSettingsHandlerId.LANGUAGE_CALLBACK, callback_data=cb.EDIT_LANGUAGE)
@with_session
async def callback_query_timezone(session: AsyncSession, update: Update, context: TMitupContext):
    # Settings-only: reads `user.lang`/`user.settings`, never the meetups/joined_links collections.
    user = await guards.current_user(update, session)

    view = views.factory.settings_set_language_view(guards.render_context(user, update, context))

    await context.api.edit_message(update=update, view=view)


@HandlersRegistry.register_callback_query(EditSettingsHandlerId.SET_LANGUAGE_CALLBACK, callback_data=cb.SET_LANGUAGE)
@with_session
async def callback_query_set_timezone(session: AsyncSession, update: Update, context: TMitupContext):
    user = await guards.current_user(update, session)

    language_id = guards.valid_callback_data(
        cb.SET_LANGUAGE.parse(context.match), EditSettingsHandlerId.SET_LANGUAGE_CALLBACK
    ).id

    if language_id >= len(SUPPORTED_LANGUAGES):
        log.warning(
            "Settings change rejected",
            user_id=user.db_id,
            setting=SettingName.LANGUAGE.value,
            requested_index=language_id,
            supported_count=len(SUPPORTED_LANGUAGES),
            reason="language_index_out_of_range",
        )
        raise InvalidLanguageError(language_id)

    old_language = user.lang
    new_language = SUPPORTED_LANGUAGES[language_id]
    user.settings.language = new_language
    await session.flush()

    log.info(
        SETTING_CHANGED_EVENT,
        user_id=user.db_id,
        setting=SettingName.LANGUAGE.value,
        old_value=old_language,
        new_value=new_language,
        source=SETTINGS_MENU_SOURCE,
    )

    view = views.factory.settings_set_language_view(
        guards.render_context(user, update, context).with_lang(new_language)
    ).with_context(SettingsMessages.LANGUAGE_SUCCESS.get(lang=new_language))

    await context.api.edit_message(update=update, view=view)
