from telegram import Message, Update
from telegram.ext.filters import MessageFilter, UpdateFilter

from mitup_bot.patreon.pairing import PAIRING_DEEP_LINK_PREFIX

from .start_payload import parse_start_payload


class PositiveNumberFilter(UpdateFilter):
    def filter(self, update: Update) -> bool:
        if not update.effective_message or not update.effective_message.text:
            return False

        return update.effective_message.text.isdigit() and int(update.effective_message.text) > 0


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
