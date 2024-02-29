from collections.abc import Generator
from unittest import mock

import pytest
from pydantic import SecretStr
from sqlmodel import Session

from mitup_bot import db
from mitup_bot.config import DbConfig


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
