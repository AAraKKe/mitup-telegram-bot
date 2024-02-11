import pytest

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
