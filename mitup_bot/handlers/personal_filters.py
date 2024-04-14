from telegram import Update
from telegram.ext.filters import UpdateFilter

from mitup_bot import db
from mitup_bot.models import User


class UserExistFilter(UpdateFilter):
    def filter(self, update: Update) -> bool:
        if update.effective_user is not None:
            with db.begin() as session:
                return User.by_tg_user_id(session, update.effective_user.id) is not None

        return False


class PositiveNumberFilter(UpdateFilter):
    def filter(self, update: Update) -> bool:
        if not update.effective_message or not update.effective_message.text:
            return False

        return update.effective_message.text.isdigit() and int(update.effective_message.text) > 0
