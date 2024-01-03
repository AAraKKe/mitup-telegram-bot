from typing import Generator
from unittest import mock

import pytest
from sqlmodel import Session

from mitup_bot.config import DbConfig
from mitup_bot.models import MitupBaseModel


@pytest.fixture
def mock_session(db_config: DbConfig) -> Generator[mock.MagicMock, None, None]:
    """
    This fixture is used to patch the interaction with the database by
    patching the Session object and yielding the patch to later be configured in
    each test as needed.

    Since we are centralizing db interaction through the base model we can easily
    patch Session there without having to worry it being instantiated anywhere else
    """
    with mock.patch("mitup_bot.models.mitup_base_model.Session") as session_patch:
        mocked_session = mock.MagicMock(spec=Session, name="MitupMockedSession")
        # Make sure the instances of the Session class are the ones
        # we will be accessing later
        session_patch.return_value = mocked_session
        mocked_session.__enter__.return_value = mocked_session

        with mock.patch("mitup_bot.models.mitup_base_model.create_engine"):
            # Patch create_engine too and make sure we are not creating an engine while
            # testing
            MitupBaseModel.set_engine(db_config)
            yield mocked_session
