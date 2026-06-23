import datetime as dt
import os
from collections.abc import Generator, Mapping
from typing import cast
from unittest import mock

import pytest
import structlog
from click.testing import CliRunner, Result
from pydantic import SecretStr
from telegram import CallbackQuery, Chat, InlineQuery, Message, Update, User
from telegram.ext import Application

from mitup_bot import db
from mitup_bot.cli.cli_commands import MitupCliCommand
from mitup_bot.config import DbConfig, MetricsConfig, MetricsEnv
from mitup_bot.models import Meetup, MeetupLocation, Settings
from mitup_bot.models import User as UserModel
from mitup_bot.monitoring import MetricsClient
from mitup_bot.monitoring.backend import configure_emf_backend
from mitup_bot.translations import SUPPORTED_LANGUAGES
from tests.helpers.constants import (
    DEFAULT_CHAT_ID,
    DEFAULT_MESSAGE_ID,
    DEFAULT_TEST_DATE,
    DEFAULT_TG_USER_PARAMS,
)
from tests.helpers.context import build_context
from tests.helpers.conversation import ConversationTester
from tests.helpers.fixtures import UpdateRequest, create_meetup, create_test_app, create_update
from tests.helpers.handler_context import HandlerContext
from tests.helpers.monitoring import MetricAssertions, make_test_metrics_client
from tests.helpers.stub_db import MockDbSession
from tests.helpers.types import CliRunner as TypeRunner
from tests.helpers.types import StubMitupContext


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--lang",
        default="en",
        choices=[*SUPPORTED_LANGUAGES, "all"],
        help="Language(s) to test. Use 'all' to run all supported languages.",
    )


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "lang" in metafunc.fixturenames:
        lang_opt: str = metafunc.config.getoption("--lang")
        langs = SUPPORTED_LANGUAGES if lang_opt == "all" else [lang_opt]
        metafunc.parametrize("lang", langs, ids=[f"lang_{lang}" for lang in langs])


# execnet's serializer dispatches on the EXACT type, not via isinstance, so a `str`/`int`/`dict`
# subclass (e.g. a `StrEnum` member such as `Env`, or an `IntEnum`) is NOT recognized and raises
# `DumpError`. Only these exact scalar types pass through unchanged.
_EXACT_SERIALIZABLE_SCALARS = (str, int, float, bool, type(None))


def make_execnet_serializable(value: object) -> object:
    """Recursively coerce *value* into something execnet can serialize, `repr`-ing anything else.

    execnet only handles the exact built-in types (str/int/float/bool/None and dict/list/tuple of
    the same), so any subclass instance (a `StrEnum` like `Env`) or custom object (a `MetricsConfig`
    bound via `log.info(..., config=...)`) must be turned into a plain string.
    """
    if type(value) in _EXACT_SERIALIZABLE_SCALARS:
        return value
    if isinstance(value, list):
        return [make_execnet_serializable(item) for item in value]
    if isinstance(value, tuple):
        return tuple(make_execnet_serializable(item) for item in value)
    if isinstance(value, dict):
        return {make_execnet_serializable(key): make_execnet_serializable(val) for key, val in value.items()}
    return repr(value)


def stringify_non_primitive_values(
    _logger: object, _method_name: str, event_dict: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    """Coerce every bound value that execnet can't serialize into a plain string.

    `render_to_log_kwargs` turns each bound key/value into a raw `LogRecord` attribute, preserving
    the original Python object. Under xdist, pytest ships each test's captured `LogRecord`s to the
    controller through execnet's serializer; a non-serializable value (e.g. a `MetricsConfig`, an
    `Env` member) raises `DumpError` and kills the worker. Coercing here mirrors what the production
    renderers do before output, so captured records stay serializable regardless of what any log
    call binds, now or in the future.
    """
    return {key: make_execnet_serializable(value) for key, value in event_dict.items()}


@pytest.fixture(autouse=True)
def deterministic_structlog() -> Generator[None]:
    """Route structlog through the stdlib logging machinery so pytest's `caplog` deterministically
    captures handler/model log output as clean LogRecords.

    Why this is needed
    ------------------
    Production `configure_logging` reconfigures structlog globally to hand off to a
    ProcessorFormatter (output goes to a stdout StreamHandler, and the LogRecord message becomes a
    wrapped blob rather than the plain event string). structlog config is process-global and
    `cache_logger_on_first_use=True`, so once any test calls `configure_logging` the new pipeline
    leaks into every later test on the same xdist worker — making `caplog` see either a rendered
    dict or nothing, depending on test order.

    This autouse fixture re-establishes a deterministic, caplog-friendly pipeline before every test:
    `render_to_log_kwargs` makes the event string the LogRecord message and turns bound key/values
    (including merge_contextvars fields) into record attributes, with `exc_info` forwarded so
    exception assertions via `caplog.text` keep working. `stringify_non_primitive_values` runs just
    before it so no captured record carries a non-serializable object across xdist's execnet channel.
    Being function-scoped and autouse, it runs after any per-test `configure_logging` call's
    teardown, guaranteeing order-independence.
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            stringify_non_primitive_values,
            structlog.stdlib.render_to_log_kwargs,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        # Don't cache: each test rebinds the pipeline, and a cached logger would pin a stale one.
        cache_logger_on_first_use=False,
    )
    yield
    structlog.reset_defaults()


@pytest.fixture
def none():
    """
    Simple drop-in fixture to get None.
    Specially in places where pytest getfixture value is used to dinamically load fixtures
    """
    return None


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
        maker_factory = mock.MagicMock(return_value=mocked_session)
        maker_patch.return_value.return_value.__enter__ = maker_factory

        with mock.patch("mitup_bot.db.create_engine"):
            # Patch create_engine to and make sure we are not creating an engine while
            # testing
            db.configure_db(db_config, skip_if_initialized=True)
            yield mocked_session
            # Unset the module level sessionmaker for the next test
            db.__sessionmaker = None


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
        user_id=user.db_id,
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
    settings.user_id = user.db_id
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
    return Chat(id=DEFAULT_CHAT_ID, type="private")


# User is not a session fixture because the language can change trom test to test
@pytest.fixture
def tg_user(request: pytest.FixtureRequest) -> User:
    # If we have requested the user_with_settings fixture
    # set the user values to the samea s the one produced
    # by the user model
    if "user_with_settings" in request.fixturenames:
        user_model = cast(UserModel, request.getfixturevalue("user_with_settings"))
        return User(
            id=user_model.tg_user_id,
            first_name=user_model.first_name,
            last_name=user_model.last_name,
            username=user_model.username,
            is_bot=False,
            language_code=user_model.lang,
        )
    return User(**DEFAULT_TG_USER_PARAMS)


@pytest.fixture
def tg_inline_query(tg_user: User) -> InlineQuery:
    return InlineQuery(id=str(DEFAULT_MESSAGE_ID), from_user=tg_user, query="example_query", offset="")


@pytest.fixture
def tg_message(tg_user: User, tg_chat: Chat) -> Message:
    return Message(DEFAULT_MESSAGE_ID, date=DEFAULT_TEST_DATE, chat=tg_chat, from_user=tg_user, text="some text")


@pytest.fixture
def tg_callback_query(tg_user: User, tg_message: Message) -> CallbackQuery:
    return CallbackQuery(
        id=str(DEFAULT_MESSAGE_ID), from_user=tg_user, message=tg_message, chat_instance="someinstance"
    )


@pytest.fixture
def update(
    tg_chat: Chat,
    tg_user: User,
    tg_message: Message,
    tg_callback_query: CallbackQuery,
    request: pytest.FixtureRequest,
):
    # Validate the request to include the necessary data
    if hasattr(request, "param"):
        data: UpdateRequest = request.param
        return create_update(data, tg_chat, tg_user, tg_message, tg_callback_query)
    else:
        # If we are not passing a parameter to the fixture lets return a message update
        return Update(DEFAULT_MESSAGE_ID, message=tg_message)


@pytest.fixture
def app() -> Application:
    return create_test_app()


@pytest.fixture
def conversation(app: Application, tg_user: User, tg_chat: Chat) -> ConversationTester:
    return ConversationTester(app=app, user=tg_user, chat=tg_chat)


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
def metrics_client() -> MetricsClient:
    return make_test_metrics_client()


@pytest.fixture
def metrics(metrics_client: MetricsClient) -> MetricAssertions:
    return MetricAssertions(metrics_client)


@pytest.fixture
def context(app: Application, update: Update, metrics_client: MetricsClient) -> StubMitupContext:
    return build_context(update, app, metrics=metrics_client)


@pytest.fixture(autouse=True, scope="session")
def configure_test_metrics():
    """Make sure metrics are always configured during test session"""
    configure_emf_backend(MetricsConfig(namespace="test", environment=MetricsEnv.STDOUT, flush_on_emission=False))


@pytest.fixture
def handler_context(update: Update, app: Application, metrics_client: MetricsClient) -> HandlerContext:
    return HandlerContext(update=update, app=app, metrics_client=metrics_client)


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
