from sqlmodel import select
from telegram import Update
from telegram.ext.filters import UpdateFilter

from mitup_bot import db
from mitup_bot.models import User
from mitup_bot.models.users import UserStatus


class MemberUserFilter(UpdateFilter):
    """Match updates whose effective user has a MEMBER `User` row.

    Gates `/start` between the existing-member flow and the new/joined-only
    re-onboarding flow. JOINED_ONLY and LEFT users intentionally fall through
    to the re-onboarding handler.
    """

    def filter(self, update: Update) -> bool:
        if update.effective_user is None:
            return False

        with db.begin() as session:
            statement = select(User).where(
                User.tg_user_id == update.effective_user.id,
                User.status == UserStatus.MEMBER,
            )
            return session.exec(statement).first() is not None


class PositiveNumberFilter(UpdateFilter):
    def filter(self, update: Update) -> bool:
        if not update.effective_message or not update.effective_message.text:
            return False

        return update.effective_message.text.isdigit() and int(update.effective_message.text) > 0
