import pytest

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
