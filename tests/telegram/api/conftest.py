import pytest

from mitup_bot.callback_data import CallbackData
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.utils.rich_message import RichContent
from mitup_bot.views import MitupView


@pytest.fixture
def default_view() -> MitupView:
    return MitupView(
        RichContent("Test message"),
        [
            [
                ButtonConfig(text="Testing", callback_data=CallbackData(entity="test_data")),
            ],
        ],
    )


@pytest.fixture
def view_with_entities() -> MitupView:
    return MitupView(
        RichContent.from_markup("<b>Bold</b> text"),
        [
            [
                ButtonConfig(text="Testing", callback_data=CallbackData(entity="test_data")),
            ],
        ],
    )
