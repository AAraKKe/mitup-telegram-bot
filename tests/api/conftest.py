import pytest
from telegram import MessageEntity

from mitup_bot.callback_data import CallbackData
from mitup_bot.utils.entities import FormattedText
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


@pytest.fixture
def view_with_entities() -> MitupView:
    entities = [MessageEntity(type=MessageEntity.BOLD, offset=0, length=4)]
    return MitupView(
        FormattedText("Bold text", entities),
        [
            [
                ButtonConfig(text="Testing", callback_data=CallbackData(entity="test_data")),
            ],
        ],
    )
