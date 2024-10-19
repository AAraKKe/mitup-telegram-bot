import pytest

from mitup_bot.utils import Emojis
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages
from mitup_bot.views import ButtonConfig, MitupView, factory


def test_edit_meeting_property_view_without_extra_options(lang: str):
    message = "Test message"
    meeting_id = 1

    view = factory.edit_meeting_property_view(lang=lang, message=message, meeting_id=meeting_id)
    expected_view = MitupView(
        description=message,
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.BACK_EDIT.get(lang=lang), callback_data=cb.EDIT_MEETING.with_id(meeting_id)
                ),
            ],
        ],
    )

    assert expected_view == view


def test_edit_meeting_property_view_with_extra_buttons(lang: str):
    message = "Test message"
    meeting_id = 1
    extra_buttons = [
        [
            ButtonConfig(text="Option 1", callback_data="option_1"),
            ButtonConfig(text="Option 2", callback_data="option_2"),
        ],
        [
            ButtonConfig(text="Option 3", callback_data="option_3"),
        ],
    ]

    view = factory.edit_meeting_property_view(
        lang=lang, message=message, meeting_id=meeting_id, extra_buttons=extra_buttons
    )
    expected_view = MitupView(
        description=message,
        keyboard=[
            [
                ButtonConfig(text="Option 1", callback_data="option_1"),
                ButtonConfig(text="Option 2", callback_data="option_2"),
            ],
            [
                ButtonConfig(text="Option 3", callback_data="option_3"),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.BACK_EDIT.get(lang=lang), callback_data=cb.EDIT_MEETING.with_id(meeting_id)
                ),
            ],
        ],
    )

    assert expected_view == view


def test_edit_meeting_property_view_with_custom_back_button(lang: str):
    message = "Test message"
    meeting_id = 1
    custom_back = ButtonConfig(text="Custom back", callback_data="custom_back")

    view = factory.edit_meeting_property_view(
        lang=lang, message=message, meeting_id=meeting_id, back_button=custom_back
    )
    expected_view = MitupView(
        description=message,
        keyboard=[
            [
                custom_back,
            ],
        ],
    )

    assert expected_view == view


@pytest.mark.parametrize("option", [True, False])
def test_flag_button(option: bool):
    callback_data = cb.SET_DEFAULT_WAITING_LIST
    text = ButtonMessages.WAITING_LIST.get(lang="en")
    emoji = Emojis.CHECK if option else Emojis.RED_CIRCLE

    button = factory.options_button(callback_data=callback_data, text=text, option=option)
    expected_button = ButtonConfig(text=f"{emoji} {text}", callback_data=callback_data)

    assert expected_button == button
