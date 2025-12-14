from unittest.mock import MagicMock

import pytest

from tests.helpers.api import MockApi


@pytest.fixture
def api():
    bot = MagicMock()
    api = MockApi()
    api.adapter = bot
    return api
