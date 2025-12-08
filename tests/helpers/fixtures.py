import datetime as dt
from dataclasses import dataclass

from telegram import InlineQuery, Location
from telegram import User as TgUser

from mitup_bot.callback_data import CallbackData
from mitup_bot.models import JoinedUsers, Meetup, MeetupLocation, Settings, User


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
    inline_query: str | InlineQuery = ""
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
    show_timezone: bool = True,
    active: bool = True,
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
        active=active,
    )

    if owner:
        meetup.owner = owner
        if owner.id:
            meetup.owner_id = owner.id
        owner.meetups.append(meetup)

    return meetup


def create_settings(
    id: int = 1,
    user: User | None = None,
    language: str = "en",
    timezone: str = "UTC",
    notification: bool = True,
    timeout: int = 1,
    default_waiting_list: bool = False,
    default_public: bool = False,
    default_allow_invitation: bool = False,
    default_incognito: bool = False,
    default_show_timezone: bool = True,
) -> Settings:
    return Settings(
        id=id,
        language=language,
        timezone=timezone,
        notification=notification,
        timeout=timeout,
        default_waiting_list=default_waiting_list,
        default_public=default_public,
        default_allow_invitation=default_allow_invitation,
        default_incognito=default_incognito,
        default_show_timezone=default_show_timezone,
    )


def create_user(
    id: int,
    username: str | None = None,
    tg_user_id: int = 123,
    first_name: str = "Test FirstName",
    last_name: str | None = None,
    is_active: bool = True,
    owned_meetings: list[Meetup] | None = None,
    settings: Settings | None = None,
) -> User:
    return User(
        id=id,
        tg_user_id=tg_user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        is_active=is_active,
        meetups=owned_meetings or [],
        settings=settings or create_settings(),
    )


def create_joined_link(
    user: User,
    meetup: Meetup,
    id: int | None = None,
    created_time: dt.datetime | None = None,
    is_waiting_list: bool = False,
    notification_sent: bool = False,
) -> JoinedUsers:
    return JoinedUsers(
        id=id,
        user_id=user.db_id,
        meetup_id=meetup.db_id,
        user=user,
        meetup=meetup,
        created_time=created_time or dt.datetime.now(dt.UTC),
        is_waiting_list=is_waiting_list,
        notification_sent=notification_sent,
    )


def telegram_user_from_user(user: User) -> TgUser:
    return TgUser(
        id=user.tg_user_id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        is_bot=False,
    )
