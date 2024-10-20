import datetime as dt
from dataclasses import dataclass

from telegram import Location

from mitup_bot.callback_data import CallbackData
from mitup_bot.models import Meetup, MeetupLocation, User


@dataclass
class UpdateRequest:
    """
    A data class representing a Telegram update we want injected as a fixture.

    Every type of update managed in the bot will include an user, a chat and a message. Since the most common type of
    update handled by the bot, message defaults to False. Otherwise, the update will be a pure message update.

    Args:
        user (bool, optional): Whether to include user information in the update request. Defaults to True.
        chat (bool, optional): Whether to include chat information in the update request. Defaults to True.
        message (bool, optional): Whether to include message information in the update request. Defaults to True.
        callback_data (CallbackData | bool, optional): Defines whether or not the update should include callback data.
            If True, a default CallbackQuery will be added. If a CallbackData object is provided, it will be used to
            generate the CallbackQuery. Defaults to False.
        inline_query (str, optional): The inline query string. Defaults to "".
    """

    user: bool = True
    chat: bool = True
    message: bool = True
    message_text: str | None = None
    location: Location | None = None
    callback_query: CallbackData | bool = False
    command: str | bool = False
    inline_query: str = ""
    inline_message_id: str | None = None


def create_meetup(
    id: int,
    title: str = "Default title",
    description: str | None = None,
    datetime: dt.datetime | None = None,
    location: MeetupLocation | None = None,
    max_members: int | None = None,
    waiting_list: bool = False,
    language: str = "en",
    owner: User | None = None,
    public: bool = False,
    invitation: bool = False,
    incognito: bool = False,
    show_timezone: bool = False,
) -> Meetup:
    meetup = Meetup(
        id=id,
        title=title,
        description=description,
        datetime=datetime,
        waiting_list=waiting_list,
        public=public,
        language=language,
        location=location or MeetupLocation(),
        max_members=max_members,
        allow_invitation=invitation,
        incognito=incognito,
        show_timezone=show_timezone,
    )

    if owner:
        owner.meetups.append(meetup)

    return meetup
