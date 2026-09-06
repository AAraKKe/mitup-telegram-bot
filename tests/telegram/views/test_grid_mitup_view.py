import pytest

import mitup_bot.utils.callbacks as cb
from mitup_bot.keyboards import ButtonConfig, Keyboard
from mitup_bot.utils.rich_message import RichContent
from mitup_bot.views import GridMitupView
from mitup_bot.views.mitup_view import arrange_in_grid


def grid_button(index: int) -> ButtonConfig:
    return ButtonConfig(text=f"action_button{index}", callback_data=cb.SHOW_MEETING.with_id(index))


def grid_buttons(count: int) -> list[ButtonConfig]:
    return [grid_button(index) for index in range(1, count + 1)]


GRID_VIEW_EVEN = GridMitupView(message=RichContent("even"), buttons=grid_buttons(4), column_size=2)
GRID_VIEW_PARTIAL = GridMitupView(message=RichContent("partial"), buttons=grid_buttons(5), column_size=2)
GRID_VIEW_THREE_COLUMNS = GridMitupView(message=RichContent("three"), buttons=grid_buttons(5), column_size=3)
GRID_VIEW_DEFAULT = GridMitupView(message=RichContent("default"), buttons=grid_buttons(3))

EXPECTED_MENU_EVEN = [
    [grid_button(1), grid_button(2)],
    [grid_button(3), grid_button(4)],
]

# Five buttons in columns of two: the final row holds the single leftover button (no padding).
EXPECTED_MENU_PARTIAL = [
    [grid_button(1), grid_button(2)],
    [grid_button(3), grid_button(4)],
    [grid_button(5)],
]

EXPECTED_MENU_THREE_COLUMNS = [
    [grid_button(1), grid_button(2), grid_button(3)],
    [grid_button(4), grid_button(5)],
]

# Default column_size is 2, so three buttons produce a full row of two plus a final single-button row.
EXPECTED_MENU_DEFAULT = [
    [grid_button(1), grid_button(2)],
    [grid_button(3)],
]


@pytest.mark.parametrize(
    "view, expected_menu",
    [
        (GRID_VIEW_EVEN, EXPECTED_MENU_EVEN),
        (GRID_VIEW_PARTIAL, EXPECTED_MENU_PARTIAL),
        (GRID_VIEW_THREE_COLUMNS, EXPECTED_MENU_THREE_COLUMNS),
        (GRID_VIEW_DEFAULT, EXPECTED_MENU_DEFAULT),
    ],
    ids=["even", "partial_final_row", "three_columns", "default_column_size"],
)
def test_grid_mitup_view_menu(view: GridMitupView, expected_menu: Keyboard):
    assert view.menu == expected_menu


def test_grid_mitup_view_empty_button_list_produces_empty_keyboard():
    view = GridMitupView(message=RichContent("empty"), buttons=[], column_size=2)
    # No buttons means no rows, so the message closes on its body alone.
    assert view.menu == []


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
