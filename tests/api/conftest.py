import pytest

from mitup_bot.api import edit_message, edit_message_view, send_message, send_message_view
from mitup_bot.utils.messages import sanitize_message
from mitup_bot.views import MitupView
from mitup_bot.views.mitup_view import ButtonConfig


@pytest.fixture
def default_view() -> MitupView:
    return MitupView(
        sanitize_message("Test message"),
        [
            [
                ButtonConfig("Testing", callback_data="test_data"),
            ],
        ],
    )


@pytest.fixture(params=[send_message, edit_message])
def api_method(request):
    return request.param


@pytest.fixture(params=[send_message_view, edit_message_view])
def api_view_method(request):
    return request.param
