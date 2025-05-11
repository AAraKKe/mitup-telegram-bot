import datetime as dt
import os
from collections.abc import Generator, Mapping
from unittest import mock

import pytest
from click.testing import CliRunner, Result
from pydantic import SecretStr
from telegram import CallbackQuery, Chat, InlineQuery, Message, MessageEntity, Update, User
from telegram.ext import Application, ApplicationBuilder, ContextTypes, ExtBot

from mitup_bot import db
from mitup_bot.callback_data import CallbackData
from mitup_bot.cli.cli_commands import MitupCliCommand
from mitup_bot.config import DbConfig, MetricsConfig, MetricsEnv
from mitup_bot.custom_context import MitupContext, MitupUserData
from mitup_bot.models import Meetup, MeetupLocation, Settings
from mitup_bot.models import User as UserModel
from mitup_bot.monitoring.metrics import configure_metrics
from mitup_bot.translations import SUPPORTED_LANGUAGES
from tests.helpers import CliRunner as TypeRunner
from tests.helpers import HandlerContext, UpdateRequest, build_context, create_meetup
from tests.helpers.stub_db import MockDbSession


@pytest.fixture
def none():
    """
    Simple drop-in fixture to get None.
    Specially in places where pytest getfixture value is used to dinamically load fixtures
    """
    return None


@pytest.fixture(params=SUPPORTED_LANGUAGES, ids=[f"lang_{lang}" for lang in SUPPORTED_LANGUAGES])
def lang(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def mock_session(db_config: DbConfig) -> Generator[MockDbSession]:
    """
    This fixture is used to patch the interaction with the database by
    patching the Session object and yielding the patch to later be configured in
    each test as needed.

    Since we are centralizing db interaction through the base model we can easily
    patch Session there without having to worry it being instantiated anywhere else
    """
    with mock.patch("mitup_bot.db.sessionmaker") as maker_patch:
        mocked_session = MockDbSession()
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


@pytest.fixture
def settings(user: UserModel) -> Settings:
    return Settings(
        id=1,
        timezone="Europe/Madrid",
        user=user,
        user_id=user.id,
    )


@pytest.fixture
def user() -> UserModel:
    return UserModel(
        id=1,
        first_name="John",
        tg_user_id=123,
        meetups=[
            create_meetup(1, "Test Meeting 1", "What a cool description. Congratulations"),
            create_meetup(2, "Test Meeting 2"),
        ],
    )


@pytest.fixture
def user_with_settings(settings: Settings, lang: str) -> UserModel:
    user = UserModel(
        id=1,
        first_name="John",
        tg_user_id=123,
        meetups=[
            create_meetup(1, "Test Meeting 1", "What a cool description. Congratulations"),
            create_meetup(2, "Test Meeting 2"),
        ],
    )
    settings.user = user
    settings.user_id = user.id
    settings.language = lang
    user.settings = settings
    return user


@pytest.fixture
def meeting(user_with_settings: UserModel) -> Meetup:
    return create_meetup(
        id=123,
        title="Test Meeting",
        description="Test Description",
        datetime=dt.datetime(1987, 7, 16, 23, 59, tzinfo=dt.UTC),
        location=MeetupLocation(name="Test Location", coordinates=(123.1, 321.1)),
        owner=user_with_settings,
        language="en",
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


def callback_query_from_callback_data(
    data: CallbackData, user: User, message: Message, inline_message_id: str | None
) -> CallbackQuery:
    if inline_message_id:
        # If the request comes with inline_message_id, the update is requested form an inline sent message
        return CallbackQuery(
            id="123", from_user=user, data=str(data), chat_instance="someinstance", inline_message_id=inline_message_id
        )
    return CallbackQuery(id="123", from_user=user, data=str(data), chat_instance="someinstance", message=message)


@pytest.fixture
def update(
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

    if data.command:
        bot_command = data.command if isinstance(data.command, str) else "test_command"
        message = Message(
            123,
            date=dt.datetime.now(),
            chat=tg_chat,
            from_user=tg_user,
            entities=[
                MessageEntity(type=MessageEntity.BOT_COMMAND, offset=0, length=len(bot_command) + 1, user=tg_user)
            ],
            text=f"/{bot_command}",
        )
        return Update(123, message=message)

    if data.callback_query:
        query = (
            callback_query_from_callback_data(data.callback_query, tg_user, tg_message, data.inline_message_id)
            if isinstance(data.callback_query, CallbackData)
            else tg_callback_query
        )
        return Update(123, callback_query=query)
    if data.inline_query:
        return Update(123, inline_query=tg_inline_query)
    if not (data.user and data.message and data.chat):
        # Any update that we manage in this bot has an associated user, chat or message:
        # - CallbackQuery
        # - Message
        # - Inlinequery
        # - Location
        # If we want to validate that the update doesn't have an user we need to provide an empty update
        return Update(123)

    # If we have a message we will al ways have a chat and a user
    # We are not dealing yet with types of updates that can have a chat without a message
    return Update(
        123,
        Message(
            123,
            date=dt.datetime.now(),
            chat=tg_chat,
            from_user=tg_user,
            text=data.message_text,
            location=data.location,
        ),
    )


@pytest.fixture
def app() -> Application:
    builder = ApplicationBuilder()
    # The bot needs to be set but we canont allow it to have defaults or
    # extra configurations will be set for the scheduler. Lets force it to not
    # have any configuration to make sure the schedulers use default values.
    bot = mock.MagicMock(spec=ExtBot)
    bot.defaults = None
    builder.bot(bot)
    builder.context_types(ContextTypes(context=MitupContext, user_data=MitupUserData))
    return builder.build()


@pytest.fixture
def cli() -> TypeRunner:
    def wrapper(
        args: str | None = None,
        input: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> Result:
        command = MitupCliCommand(no_args_is_help=True)
        runner = CliRunner()
        return runner.invoke(command, args=args, env=env, input=input)

    return wrapper


@pytest.fixture
def context(app: Application, update: Update) -> MitupContext:
    return build_context(update, app)


@pytest.fixture(autouse=True, scope="session")
def configure_test_metrics():
    """Make sure metrics are always configured during test session"""
    configure_metrics(MetricsConfig(namespace="test", environment=MetricsEnv.STDOUT, flush_on_emission=False))


@pytest.fixture
def handler_context(update: Update, app: Application) -> HandlerContext:
    return HandlerContext(update=update, app=app)


@pytest.fixture(autouse=True, scope="session")
def test_env():
    """
    Fixture that sets up test-specific environment variables.
    These variables will be available during test execution only.
    """
    # Store original environment variables
    original_env = dict(os.environ)

    # Set test-specific environment variables
    test_env_vars = {
        "TEST_DATABASE_URL": "postgresql://test:test@localhost:5432/test_db",
        "TEST_TELEGRAM_BOT_TOKEN": "test_token_123",
        "TEST_METRICS_ENABLED": "false",
        "TEST_ENVIRONMENT": "test",
    }

    # Update environment with test variables
    os.environ.update(test_env_vars)

    yield

    # Restore original environment variables
    os.environ.clear()
    os.environ.update(original_env)
