from telegram import Update

from mitup_bot.exceptions import EffectiveUserNotSet

from . import JoinedUsers, Meetup, Settings, User
from .users import UserStatus


def user_from_update(update: Update, status: UserStatus = UserStatus.MEMBER) -> User:
    """Given an update with an effective user, return a new User instance with properties from the effective user."""
    if update.effective_user is None:
        raise EffectiveUserNotSet(update)

    return User(
        tg_user_id=update.effective_user.id,
        first_name=update.effective_user.first_name,
        last_name=update.effective_user.last_name,
        username=update.effective_user.username,
        status=status,
        settings=Settings(),
    )


def joined_link(meeting: Meetup, user: User) -> JoinedUsers | None:
    """
    Return a new JoinedUsers instance linking the given user to the given meeting.

    If the meeting is full, None will be returned unless waiting list is enabled.
    """
    if meeting.join_allowed():
        return JoinedUsers(user=user, meetup=meeting, is_waiting_list=meeting.full)
    return None


def promote_from_waiting_list(meeting: Meetup) -> list[JoinedUsers]:
    """
    Handle promotions from the waiting list to the joined list for the given meeting.

    If the meeting is not full and waiting list is enabled, users will be promoted from the waiting list to the joined
    based on the order they joined the waiting list.

    Args:
        meeting (Meetup): The meeting to be used to handle promotions.

    Returns:
        list[JoinedUsers]: The list of users that were promoted from the waiting list.
    """
    # If the meeting is not full anymore promote from waiting list to joined
    if meeting.join_allowed() and (waiting_links := meeting.waiting_links()):
        assert meeting.max_members is not None

        to_promote = min(meeting.n_waiting, meeting.max_members - meeting.n_participants)
        promoted = []

        for link in waiting_links[:to_promote]:
            link.is_waiting_list = False
            promoted.append(link)

        return promoted
    return []
