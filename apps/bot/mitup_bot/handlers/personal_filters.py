from telegram import Message, Update
from telegram.ext.filters import MessageFilter, UpdateFilter

from mitup_bot.patreon.pairing import PAIRING_DEEP_LINK_PREFIX

from .start_payload import parse_start_payload


def positive_number(update: Update) -> int | None:
    """The update's message text read as a positive whole number, or None when it isn't one."""
    text = update.effective_message.text if update.effective_message else None
    if not text or not text.isdigit():
        return None

    value = int(text)
    return value if value > 0 else None


class PositiveNumberFilter(UpdateFilter):
    def filter(self, update: Update) -> bool:
        return positive_number(update) is not None


class BoundedPositiveNumberFilter(UpdateFilter):
    """Match a positive whole number that is at most `maximum`.

    A number above the bound does not match, so it reaches the conversation's invalid-input
    fallback instead of the handler that stores it.
    """

    def __init__(self, maximum: int):
        super().__init__()
        self.maximum = maximum

    def filter(self, update: Update) -> bool:
        value = positive_number(update)
        return value is not None and value <= self.maximum


class PatreonPairingStartFilter(MessageFilter):
    """Match a `/start` carrying a Patreon pairing payload.

    Routing this ahead of the onboarding conversation is a decision made during handler matching,
    which runs synchronously and must not touch the database. It doesn't need to: the payload is in
    the message text, so the whole decision is string work.
    """

    def filter(self, message: Message) -> bool:
        if message.text is None:
            return False
        # `/start@thebot payload` in a group carries the bot name on the command itself.
        command, *args = message.text.split()
        if not command.startswith("/start"):
            return False
        payload = parse_start_payload(args)
        return payload is not None and payload.kind == PAIRING_DEEP_LINK_PREFIX


class RichMessageFilter(MessageFilter):
    """Match a Telegram rich message: a Bot API message kind that carries no `text`."""

    def filter(self, message: Message) -> bool:
        # PTB does not model the `rich_message` field, so its payload lands in `api_kwargs`. The
        # `getattr` leg keeps matching once a PTB upgrade promotes the field to a real attribute.
        return getattr(message, "rich_message", None) is not None or "rich_message" in message.api_kwargs
