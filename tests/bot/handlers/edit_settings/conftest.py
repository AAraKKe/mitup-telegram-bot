from unittest import mock

import pytest


@pytest.fixture
def get_timezone_from_api():
    with mock.patch("mitup_bot.timezone_api.get_timezone_by_address") as timezone_patch:
        yield timezone_patch


@pytest.fixture
def get_location_from_api():
    with mock.patch("mitup_bot.timezone_api.get_timezone_by_location") as location_patch:
        yield location_patch
