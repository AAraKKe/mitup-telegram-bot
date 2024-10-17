from unittest import mock

import pytest

from tests.helpers import MockApi


@pytest.fixture
def api():
    with MockApi.start("mitup_bot.handlers.messages") as api:
        yield api


@pytest.fixture
def get_timezone_from_api():
    with mock.patch("mitup_bot.timezone_api.get_timezone_by_address") as timezone_patch:
        yield timezone_patch


@pytest.fixture
def get_location_from_api():
    with mock.patch("mitup_bot.timezone_api.get_timezone_by_location") as location_patch:
        yield location_patch
