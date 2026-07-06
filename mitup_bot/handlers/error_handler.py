import structlog
from rich.console import Console
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import Update
from telegram.error import BadRequest

from mitup_bot import db
from mitup_bot.config import Env
from mitup_bot.exceptions import GuardError, InactiveUserInteraction
from mitup_bot.models import User
from mitup_bot.monitoring import MetricKey
from mitup_bot.translations import TranslationEngine
from mitup_bot.utils.messages import CommonMessages
from mitup_bot.utils.mitup_types import TMitupContext
from mitup_bot.views import factory

console = Console()

log = structlog.get_logger(__name__)

# Errors that can be suppressed and ignored
SUPPRESSED_EXCEPTIONS: dict[type, set[str]] = {
    BadRequest: {
        # This happens when a message is deleted
        "Message to edit not found",
    }
}


@db.with_session
async def handle_inactive_user(session: AsyncSession, context: TMitupContext, tg_user_id: int):
    # InactiveUserInteraction carries the TELEGRAM user id (see the api_wrapper raise sites);
    # filtering on the internal primary key silently matched nothing — or the wrong user.
    if (
        user := (await session.exec(select(User).where(User.tg_user_id == tg_user_id))).first()
    ) and user.mark_inactive():
        context.emit_metric(MetricKey.INACTIVE_USER_SET, 1, include_handler_properties=False)


def should_ignore_error(error: Exception) -> bool:
    if type(error) not in SUPPRESSED_EXCEPTIONS:
        return False

    return str(error) in SUPPRESSED_EXCEPTIONS[type(error)]


@db.with_session
async def resolve_lang(session: AsyncSession, update: Update | None) -> str:
    """Best-effort lookup of the effective user's language.

    Falls back to the project default language when the update, user, or DB record is missing.
    """
    if (
        update is not None
        and update.effective_user
        and (user := await User.by_tg_user_id(session, update.effective_user.id))
    ):
        return user.lang
    return TranslationEngine.FALLBACK_LANG


async def send_guard_notification(context: TMitupContext, update: Update, lang: str):
    view = factory.main_menu_view(lang=lang, message=CommonMessages.UNEXPECTED_ERROR.get(lang=lang))
    if update.callback_query is not None:
        await context.api.answer_callback_query(update=update, text="", show_alert=False)
    await context.api.send_message(update=update, view=view)


async def notify_guard_error(context: TMitupContext):
    """Send the user back to the main menu after a guard failure; never raises.

    This runs inside the last-resort error handler, so the whole path — lang resolution, view build,
    message render, callback answer and send — is best-effort. Any failure (missing chat, blocked bot,
    absent translation file, DB error) is swallowed and logged, since re-raising here would escape to
    ``process_update`` as an unhandled second fault.
    """
    update = context.telegram_update
    if update is None:
        return

    try:
        lang = await resolve_lang(update)
        await send_guard_notification(context, update, lang)
    except Exception:
        log.debug("Failed to deliver guard-error notification to the user.", exc_info=True)


async def handler(context: TMitupContext, error: Exception, env: Env):
    # This is the error handler that will receive every exception that is raised

    if should_ignore_error(error):
        return

    if isinstance(error, InactiveUserInteraction) and error.private:
        await handle_inactive_user(context, error.tg_user_id)
        return

    # Emit an error-class-specific fault metric plus the general aggregate FAULT. Both are
    # dimensionless — the handler identity rides as an EMF property — so the
    # dimensionless FAULT the infra alarms read is emitted exactly once per fault.
    error_class = error.__class__.__name__
    context.emit_metric(MetricKey.FAULT.with_prefix(error_class), 1)
    context.emit_metric(MetricKey.FAULT, 1)

    context.metrics.add_stack_trace()

    # Guard failures are internal faults the user should not see silently: notify them and redirect
    # to the main menu. The fault metric above already carries the per-guard suffix.
    if isinstance(error, GuardError):
        await notify_guard_error(context)

    # If we are in development mode, lets print the exception when it happens
    if env is Env.DEV:  # pragma: no cover
        # Print exception with rich logger
        log.exception("An error occurred while handling the update")
