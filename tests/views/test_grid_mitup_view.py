import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import mitup_bot.utils.callbacks as cb
from mitup_bot.views import ButtonConfig, GridMitupView
from mitup_bot.views.mitup_view import arrange_in_grid


def grid_buttons(count: int) -> list[ButtonConfig]:
    return [
        ButtonConfig(text=f"action_button{i}", callback_data=cb.SHOW_MEETING.with_id(i)) for i in range(1, count + 1)
    ]


GRID_VIEW_EVEN = GridMitupView(description="even", buttons=grid_buttons(4), column_size=2)
GRID_VIEW_PARTIAL = GridMitupView(description="partial", buttons=grid_buttons(5), column_size=2)
GRID_VIEW_THREE_COLUMNS = GridMitupView(description="three", buttons=grid_buttons(5), column_size=3)
GRID_VIEW_DEFAULT = GridMitupView(description="default", buttons=grid_buttons(3))

EXPECTED_MARKUP_EVEN = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton("action_button1", callback_data="show;meeting:1"),
            InlineKeyboardButton("action_button2", callback_data="show;meeting:2"),
        ],
        [
            InlineKeyboardButton("action_button3", callback_data="show;meeting:3"),
            InlineKeyboardButton("action_button4", callback_data="show;meeting:4"),
        ],
    ]
)

# Five buttons in columns of two: the final row holds the single leftover button (no padding).
EXPECTED_MARKUP_PARTIAL = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton("action_button1", callback_data="show;meeting:1"),
            InlineKeyboardButton("action_button2", callback_data="show;meeting:2"),
        ],
        [
            InlineKeyboardButton("action_button3", callback_data="show;meeting:3"),
            InlineKeyboardButton("action_button4", callback_data="show;meeting:4"),
        ],
        [InlineKeyboardButton("action_button5", callback_data="show;meeting:5")],
    ]
)

EXPECTED_MARKUP_THREE_COLUMNS = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton("action_button1", callback_data="show;meeting:1"),
            InlineKeyboardButton("action_button2", callback_data="show;meeting:2"),
            InlineKeyboardButton("action_button3", callback_data="show;meeting:3"),
        ],
        [
            InlineKeyboardButton("action_button4", callback_data="show;meeting:4"),
            InlineKeyboardButton("action_button5", callback_data="show;meeting:5"),
        ],
    ]
)

# Default column_size is 2, so three buttons produce a full row of two plus a final single-button row.
EXPECTED_MARKUP_DEFAULT = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton("action_button1", callback_data="show;meeting:1"),
            InlineKeyboardButton("action_button2", callback_data="show;meeting:2"),
        ],
        [InlineKeyboardButton("action_button3", callback_data="show;meeting:3")],
    ]
)


@pytest.mark.parametrize(
    "view, expected_markup",
    [
        (GRID_VIEW_EVEN, EXPECTED_MARKUP_EVEN),
        (GRID_VIEW_PARTIAL, EXPECTED_MARKUP_PARTIAL),
        (GRID_VIEW_THREE_COLUMNS, EXPECTED_MARKUP_THREE_COLUMNS),
        (GRID_VIEW_DEFAULT, EXPECTED_MARKUP_DEFAULT),
    ],
    ids=["even", "partial_final_row", "three_columns", "default_column_size"],
)
def test_grid_mitup_view_markup(view: GridMitupView, expected_markup: InlineKeyboardMarkup):
    assert view.markup == expected_markup


def test_grid_mitup_view_empty_button_list_produces_empty_keyboard():
    view = GridMitupView(description="empty", buttons=[], column_size=2)
    # No buttons means no rows, and a keyboard with no rows yields no markup.
    assert view.keyboard == []
    assert view.markup is None


@pytest.mark.parametrize(
    "button_count, column_size, expected_row_lengths",
    [
        (5, 2, [2, 2, 1]),
        (3, 1, [1, 1, 1]),
        (3, 5, [3]),
    ],
    ids=["partial_final_row", "single_column", "column_size_larger_than_count"],
)
def test_arrange_in_grid_row_lengths(button_count: int, column_size: int, expected_row_lengths: list[int]):
    buttons = grid_buttons(button_count)
    rows = arrange_in_grid(buttons, column_size)
    # Rows grow to fill the grid; the trailing partial row keeps only the leftover button (no padding).
    assert [len(row) for row in rows] == expected_row_lengths
