import datetime as dt
from dataclasses import dataclass
from unittest import mock

from telegram import CallbackQuery, Chat, InlineQuery, Location, Message, MessageEntity, Update
from telegram import User as TgUser
from telegram.ext import Application, ApplicationBuilder, ContextTypes, ExtBot

from mitup_bot.callback_data import CallbackData
from mitup_bot.custom_context import MitupContext, MitupUserData
from mitup_bot.models import JoinedUsers, Meetup, MeetupLocation, Settings, User
from mitup_bot.models import Message as MeetupMessage
from mitup_bot.models.users import UserStatus
from tests.helpers.constants import (
    DEFAULT_CHAT_ID,
    DEFAULT_MESSAGE_ID,
    DEFAULT_TEST_DATE,
    DEFAULT_TG_USER_PARAMS,
)


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
    entities: list[MessageEntity] | None = None
    location: Location | None = None
    callback_query: CallbackData | bool = False
    command: str | bool = False
    command_args: str | None = None
    inline_query: str | InlineQuery = ""
    inline_message_id: str = "some_inline_message_id"
    from_bot_chat: bool = True


def create_meetup(
    id: int,
    title: str = "Default title",
    description: str | None = None,
    datetime: dt.datetime | None = None,
    created_time: dt.datetime | None = None,
    location: MeetupLocation | None = None,
    max_members: int | None = None,
    waiting_list: bool = False,
    language: str | None = "en",
    owner: User | None = None,
    public: bool = False,
    invitation: bool = False,
    incognito: bool = False,
    active: bool = True,
) -> Meetup:
    meetup = Meetup(
        id=id,
        title=title,
        description=description,
        datetime=datetime,
        created_time=created_time or dt.datetime.now(dt.UTC),
        waiting_list=waiting_list,
        public=public,
        language=language,
        location=location or MeetupLocation(),
        max_members=max_members,
        allow_invitation=invitation,
        incognito=incognito,
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
    )


def create_user(
    id: int,
    username: str | None = None,
    tg_user_id: int = 123,
    first_name: str = "Test FirstName",
    last_name: str | None = None,
    status: UserStatus = UserStatus.MEMBER,
    owned_meetings: list[Meetup] | None = None,
    settings: Settings | None = None,
) -> User:
    return User(
        id=id,
        tg_user_id=tg_user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        status=status,
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
    invited_by: User | None = None,
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
        invited_by=invited_by,
    )


def create_message(
    id: int = 1,
    inline_message_id: str | None = "some_inline_message_id",
    chat_instance: str | None = None,
    meetup_id: int = 1,
    message_id: int | None = None,
    chat_id: int | None = None,
) -> MeetupMessage:
    return MeetupMessage(
        id=id,
        inline_message_id=inline_message_id,
        chat_instance=chat_instance,
        meetup_id=meetup_id,
        message_id=message_id,
        chat_id=chat_id,
    )


def owner_with_meeting(meeting_id: int = 1) -> tuple[User, Meetup]:
    """Build a user owning a single meeting. Returns (user, meeting)."""
    meeting = create_meetup(id=meeting_id, title="Test Meeting")
    user = create_user(id=1, tg_user_id=123, owned_meetings=[meeting], settings=Settings(id=1))
    return user, meeting


def telegram_user_from_user(user: User) -> TgUser:
    return TgUser(
        id=user.tg_user_id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        is_bot=False,
    )


def callback_query_from_callback_data(
    data: CallbackData, user: TgUser, message: Message, inline_message_id: str | None, is_bot_chat: bool
) -> CallbackQuery:
    if is_bot_chat:
        # Bot chats have a message attached to it. Inline messages from outside the bot chat do not
        return CallbackQuery(
            id=str(DEFAULT_MESSAGE_ID), from_user=user, data=str(data), chat_instance="someinstance", message=message
        )
    return CallbackQuery(
        id=str(DEFAULT_MESSAGE_ID),
        from_user=user,
        data=str(data),
        chat_instance="someinstance",
        inline_message_id=inline_message_id,
    )


def create_test_app() -> Application:
    builder = ApplicationBuilder()
    # The bot needs to be set but we canont allow it to have defaults or
    # extra configurations will be set for the scheduler. Lets force it to not
    # have any configuration to make sure the schedulers use default values.
    bot = mock.MagicMock(spec=ExtBot)
    bot.defaults = None
    builder.bot(bot)
    builder.context_types(ContextTypes(context=MitupContext, user_data=MitupUserData))
    return builder.build()


def create_update(
    request: UpdateRequest,
    tg_chat: Chat | None = None,
    tg_user: TgUser | None = None,
    tg_message: Message | None = None,
    tg_callback_query: CallbackQuery | None = None,
) -> Update:
    # Use defaults if not provided to make fixture injection easier
    chat = tg_chat or Chat(id=DEFAULT_CHAT_ID, type="private")
    user = tg_user or TgUser(**DEFAULT_TG_USER_PARAMS)

    if request.command:
        bot_command = request.command if isinstance(request.command, str) else "test_command"
        text = f"/{bot_command}"
        if request.command_args:
            text = f"{text} {request.command_args}"
        message = Message(
            DEFAULT_MESSAGE_ID,
            date=DEFAULT_TEST_DATE,
            chat=chat,
            from_user=user,
            entities=[MessageEntity(type=MessageEntity.BOT_COMMAND, offset=0, length=len(bot_command) + 1, user=user)],
            text=text,
        )
        return Update(DEFAULT_MESSAGE_ID, message=message)

    if request.callback_query:
        # If no message provided for callback, create a minimal one
        msg = tg_message or Message(DEFAULT_MESSAGE_ID, date=DEFAULT_TEST_DATE, chat=chat, from_user=user, text="")

        # If no default callback query provided, create one
        default_cb = tg_callback_query or CallbackQuery(
            id=str(DEFAULT_MESSAGE_ID), from_user=user, message=msg, chat_instance="inst"
        )

        query = (
            callback_query_from_callback_data(
                request.callback_query, user, msg, request.inline_message_id, request.from_bot_chat
            )
            if isinstance(request.callback_query, CallbackData)
            else default_cb
        )
        return Update(DEFAULT_MESSAGE_ID, callback_query=query)
    if request.inline_query:
        if isinstance(request.inline_query, InlineQuery):
            return Update(DEFAULT_MESSAGE_ID, inline_query=request.inline_query)

        # If a string is provided for inline_query but no tg_inline_query object, construct one
        query_text = request.inline_query
        return Update(
            DEFAULT_MESSAGE_ID,
            inline_query=InlineQuery(id=str(DEFAULT_MESSAGE_ID), from_user=user, query=query_text, offset=""),
        )
    if not (request.user and request.message and request.chat):
        # Any update that we manage in this bot has an associated user, chat or message:
        # - CallbackQuery
        # - Message
        # - Inlinequery
        # - Location
        # If we want to validate that the update doesn't have an user we need to provide an empty update
        return Update(DEFAULT_MESSAGE_ID)

    # If we have a message we will al ways have a chat and a user
    # We are not dealing yet with types of updates that can have a chat without a message
    return Update(
        DEFAULT_MESSAGE_ID,
        Message(
            DEFAULT_MESSAGE_ID,
            date=DEFAULT_TEST_DATE,
            chat=chat,
            from_user=user,
            text=request.message_text,
            entities=request.entities,
            location=request.location,
        ),
    )
