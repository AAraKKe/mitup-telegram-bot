import datetime as dt
from collections.abc import Generator
from unittest import mock

import pytest
from pydantic import SecretStr
from sqlmodel import Session
from telegram import CallbackQuery, Chat, InlineQuery, Message, Update, User
from telegram.ext import ApplicationBuilder, ContextTypes, ExtBot

from mitup_bot import db
from mitup_bot.config import DbConfig
from mitup_bot.custom_context import MitupContext, MitupUserData
from mitup_bot.models import Meetup, MeetupLocation, Settings
from mitup_bot.models import User as UserModel
from tests.helpers import UpdateRequest


@pytest.fixture
def mock_session(db_config: DbConfig) -> Generator[mock.MagicMock, None, None]:
    """
    This fixture is used to patch the interaction with the database by
    patching the Session object and yielding the patch to later be configured in
    each test as needed.

    Since we are centralizing db interaction through the base model we can easily
    patch Session there without having to worry it being instantiated anywhere else
    """
    with mock.patch("mitup_bot.db.sessionmaker") as maker_patch:
        mocked_session = mock.MagicMock(spec=Session, name="MitupMockedSession")
        # Setup a factory that returns our mocked_session
        maker_patch.return_value = lambda: mocked_session

        with mock.patch("mitup_bot.db.create_engine"):
            # Patch create_engine to and make sure we are not creating an engine while
            # testing
            db.configure_db(db_config)
            yield mocked_session
            # Unset the module level sessionmaker for the next test
            db.__sessionmaker = None  # type: ignore


@pytest.fixture(scope="session")
def db_config() -> DbConfig:
    return DbConfig(
        username="user",
        password=SecretStr("password"),
        url="testhost",
        database="db",
    )


@pytest.fixture(scope="function")
def user() -> UserModel:
    return UserModel(
        id=1,
        first_name="John",
        tg_user_id=123,
        settings=Settings(timezone="America/New_York"),
        meetups=[
            Meetup(id=1, title="Test Meeting 1", description="What a cool description. Congratulations!"),
            Meetup(id=2, title="Test Meeting 2"),
        ],
    )


@pytest.fixture(scope="function")
def meeting(user: UserModel) -> Meetup:
    return Meetup(
        id=123,
        title="Test Meeting",
        description="Test Description",
        date=dt.date.today(),
        location=MeetupLocation(name="Test Location", coordinates=(123.1, 321.1)),
        owner=user,
    )


@pytest.fixture(scope="session")
def tg_chat() -> Chat:
    return Chat(id=123, type="private")


@pytest.fixture(scope="session")
def tg_user() -> User:
    return User(id=123, first_name="MitupUser", is_bot=False, username="mitupsername")


@pytest.fixture(scope="session")
def tg_inline_query(tg_user: User) -> InlineQuery:
    return InlineQuery(id="123", from_user=tg_user, query="example_query", offset="")


@pytest.fixture(scope="session")
def tg_message(tg_user: User, tg_chat: Chat) -> Message:
    return Message(123, date=dt.datetime.now(), chat=tg_chat, from_user=tg_user, text="some text")


@pytest.fixture(scope="session")
def tg_callback_query(tg_user: User, tg_message: Message) -> CallbackQuery:
    return CallbackQuery(id="123", from_user=tg_user, message=tg_message, chat_instance="someinstance")


@pytest.fixture
def tg_update(
    tg_chat: Chat,
    tg_user: User,
    tg_inline_query: InlineQuery,
    tg_message: Message,
    tg_callback_query: CallbackQuery,
    request: pytest.FixtureRequest,
):
    # Validate the request to include the necessary data
    if hasattr(request, "param"):
        data: UpdateRequest = request.param
    else:
        # If we are not passing a parameter to the fixture lets return a message update
        return Update(123, message=tg_message)

    if data.callback_query:
        return Update(123, callback_query=tg_callback_query)
    if data.inline_query:
        return Update(123, inline_query=tg_inline_query)
    if not (data.user and data.message and data.chat):
        # Any update that we manage in this bot has an associated user, chat or message:
        # - CallbackQuery
        # - Message
        # - Inlinequery
        # If we want to validate that the update doesn't have an user we need to provide an empty update
        return Update(123)
    # If we have a message we will al ways have a chat and a user
    # We are not dealing yet with types of updates that can have a chat without a message
    return Update(123, Message(123, date=dt.datetime.now(), chat=tg_chat, from_user=tg_user))


@pytest.fixture
def tg_context() -> MitupContext:
    bot = mock.MagicMock(spec=ExtBot)

    builder = ApplicationBuilder()
    builder.bot(bot)
    builder.context_types(ContextTypes(context=MitupContext, user_data=MitupUserData))

    return MitupContext(builder.build())


@pytest.fixture()
def context(tg_update: Update, tg_context: MitupContext):
    return tg_context.from_update(tg_update, tg_context.application)
