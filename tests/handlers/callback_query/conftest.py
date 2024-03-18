import pytest

from tests.helpers import MockApi


@pytest.fixture
def api():
    with MockApi.start("mitup_bot.handlers.callback_query") as api:
        yield api
