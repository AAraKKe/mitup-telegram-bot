from enum import auto

from telegram import Update

from mitup_bot.handler_id import HandlerId
from mitup_bot.mitup_types import TMitupContext
from mitup_bot.utils import callbacks as cb

from .registry import HandlersRegistry


class EmptyCallbackHandlerId(HandlerId):
    EMPTY_CALLBACK = auto()


@HandlersRegistry.register_callback_query(
    handler_id=EmptyCallbackHandlerId.EMPTY_CALLBACK,
    callback_data=cb.EMPTY,
)
async def callback_query_empty(update: Update, context: TMitupContext):
    """Absorb a tap on a button that is only a label.

    Telegram accepts no inline button without callback data, so the decorative cells of
    `CalendarKeyboard` — the weekday headers, the days outside the displayed month, and the
    month/year label between the navigation arrows — carry `cb.EMPTY`. Binding it keeps those taps
    away from the unrouted-callback fallback, which reads unmatched data as a button shipped with no
    handler and fails the interaction.

    The body is empty because the caller is owed nothing beyond the acknowledgement that clears the
    pressed button's spinner, and the registry's `auto_answer` sends it — no text, no alert, so the
    screen is left exactly as it was.
    """
