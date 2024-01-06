from telegram import Message
from telegram.ext.filters import MessageFilter
from mitup_bot.models import User


class UserExistFilter(MessageFilter):
    def filter(self, message: Message) -> bool:
        user = message.from_user.id
        with User.open_session():
            return User.find_by_tg_user_id(user) is not None
