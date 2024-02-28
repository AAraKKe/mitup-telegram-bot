from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import mitup_bot.utils.callbacks as cb
from mitup_bot.views import ButtonConfig, MitupView


def test_mitup_view_markup():
    expected_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton("button1", callback_data="show;meeting:12"),
                InlineKeyboardButton("button2", callback_data="show;meeting:13"),
            ],
            [
                InlineKeyboardButton("button3", callback_data="show;meeting:14"),
                InlineKeyboardButton("button4", callback_data="show;meeting:15"),
            ],
            [
                InlineKeyboardButton("sharebutton", switch_inline_query="meeting:12"),
            ],
        ]
    )

    view = MitupView(
        "Some message",
        keyboard=[
            [
                ButtonConfig(text="button1", callback_data=cb.SHOW_MEETING.with_id(12)),
                ButtonConfig(text="button2", callback_data=cb.SHOW_MEETING.with_id(13)),
            ],
            [
                ButtonConfig(text="button3", callback_data=cb.SHOW_MEETING.with_id(14)),
                ButtonConfig(text="button4", callback_data=cb.SHOW_MEETING.with_id(15)),
            ],
            [ButtonConfig(text="sharebutton", switch_inline_query="meeting:12")],
        ],
    )

    assert expected_keyboard == view.markup
