import datetime as dt
from dataclasses import dataclass

from telegram import Location

from mitup_bot.callback_data import CallbackData
from mitup_bot.models.meetups import Meetup


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
    description: str = "Default description",
    datetime: dt.datetime | None = None,
) -> Meetup:
    return Meetup(id=id, title=title, description=description, datetime=datetime)
