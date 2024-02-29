from telegram import Update
from telegram.ext.filters import UpdateFilter

from mitup_bot import db
from mitup_bot.models import User


class UserExistFilter(UpdateFilter):
    def filter(self, update: Update) -> bool:
        if update.effective_user is not None:
            with db.begin() as session:
                return User.find_by_tg_user_id(session, update.effective_user.id) is not None

        return False
