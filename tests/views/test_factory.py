from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages
from mitup_bot.views import ButtonConfig, MitupView, factory


def test_edit_meeting_property_view_without_extra_options():
    message = "Test message"
    meeting_id = 1

    view = factory.edit_meeting_property_view(message, meeting_id)
    expected_view = MitupView(
        description=message,
        keyboard=[
            [
                ButtonConfig(text=ButtonMessages.BACK_EDIT.get(), callback_data=cb.EDIT_MEETING.with_id(meeting_id)),
            ],
        ],
    )

    assert expected_view == view


def test_edit_meeting_property_view_with_extra_buttons():
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

    view = factory.edit_meeting_property_view(message, meeting_id, extra_buttons=extra_buttons)
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
                ButtonConfig(text=ButtonMessages.BACK_EDIT.get(), callback_data=cb.EDIT_MEETING.with_id(meeting_id)),
            ],
        ],
    )

    assert expected_view == view


def test_edit_meeting_property_view_with_custom_back_button():
    message = "Test message"
    meeting_id = 1
    custom_back = ButtonConfig(text="Custom back", callback_data="custom_back")

    view = factory.edit_meeting_property_view(message, meeting_id, back_button=custom_back)
    expected_view = MitupView(
        description=message,
        keyboard=[
            [
                custom_back,
            ],
        ],
    )

    assert expected_view == view
