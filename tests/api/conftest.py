import pytest

from mitup_bot.api import edit_message, send_message
from mitup_bot.callback_data import CallbackData
from mitup_bot.views import MitupView
from mitup_bot.views.mitup_view import ButtonConfig


@pytest.fixture
def default_view() -> MitupView:
    return MitupView(
        "Test message",
        [
            [
                ButtonConfig(text="Testing", callback_data=CallbackData(entity="test_data")),
            ],
        ],
    )


@pytest.fixture(params=[send_message, edit_message])
def api_method(request):
    return request.param
