from telegram import Update
from telegram.ext.filters import UpdateFilter
from mitup_bot.models import User


class UserExistFilter(UpdateFilter):
    def filter(self, update: Update) -> bool:
        if update.effective_user is not None:
            user = update.effective_user.id
            with User.open_session():
                return User.find_by_tg_user_id(user) is not None

        return False
