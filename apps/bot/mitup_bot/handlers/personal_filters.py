from telegram import Message, Update
from telegram.ext.filters import MessageFilter, UpdateFilter


class PositiveNumberFilter(UpdateFilter):
    def filter(self, update: Update) -> bool:
        if not update.effective_message or not update.effective_message.text:
            return False

        return update.effective_message.text.isdigit() and int(update.effective_message.text) > 0


class RichMessageFilter(MessageFilter):
    """Match a Telegram rich message: a Bot API message kind that carries no `text`."""

    def filter(self, message: Message) -> bool:
        # PTB does not model the `rich_message` field, so its payload lands in `api_kwargs`. The
        # `getattr` leg keeps matching once a PTB upgrade promotes the field to a real attribute.
        return getattr(message, "rich_message", None) is not None or "rich_message" in message.api_kwargs
