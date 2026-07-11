from telegram import Update
from telegram.ext.filters import UpdateFilter


class PositiveNumberFilter(UpdateFilter):
    def filter(self, update: Update) -> bool:
        if not update.effective_message or not update.effective_message.text:
            return False

        return update.effective_message.text.isdigit() and int(update.effective_message.text) > 0
