from unittest.mock import MagicMock

import pytest

from tests.helpers.api import MockApi


@pytest.fixture
def api() -> MockApi:
    bot = MagicMock()
    api = MockApi()
    api.adapter = bot
    return api
