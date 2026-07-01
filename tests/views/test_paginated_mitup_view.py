import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import mitup_bot.utils.callbacks as cb
from mitup_bot.utils import ButtonMessages
from mitup_bot.views import ButtonConfig, PaginatedMitupView

PAGINATED_VIEW_UNIQUE = PaginatedMitupView(
    description="unique",
    buttons=[
        ButtonConfig(text="action_button1", callback_data=cb.SHOW_MEETING.with_id(1)),
        ButtonConfig(text="action_button2", callback_data=cb.SHOW_MEETING.with_id(2)),
        ButtonConfig(text="action_button3", callback_data=cb.SHOW_MEETING.with_id(3)),
        ButtonConfig(text="action_button4", callback_data=cb.SHOW_MEETING.with_id(4)),
    ],
    page_number=1,
    column_size=2,
    row_size=2,
    navigation_callback_data=cb.SHOW_ACTIVE_MEETING_PAGE,
)

PAGINATED_VIEW_FIRST = PaginatedMitupView(
    description="first",
    buttons=[
        ButtonConfig(text="action_button1", callback_data=cb.SHOW_MEETING.with_id(1)),
        ButtonConfig(text="action_button2", callback_data=cb.SHOW_MEETING.with_id(2)),
        ButtonConfig(text="action_button3", callback_data=cb.SHOW_MEETING.with_id(3)),
        ButtonConfig(text="action_button4", callback_data=cb.SHOW_MEETING.with_id(4)),
        ButtonConfig(text="action_button5", callback_data=cb.SHOW_MEETING.with_id(5)),
        ButtonConfig(text="action_button6", callback_data=cb.SHOW_MEETING.with_id(6)),
    ],
    page_number=1,
    column_size=3,
    row_size=1,
    navigation_callback_data=cb.SHOW_ACTIVE_MEETING_PAGE,
)

PAGINATED_VIEW_MIDDLE = PaginatedMitupView(
    description="middle",
    buttons=[
        ButtonConfig(text="action_button1", callback_data=cb.SHOW_MEETING.with_id(1)),
        ButtonConfig(text="action_button2", callback_data=cb.SHOW_MEETING.with_id(2)),
        ButtonConfig(text="action_button3", callback_data=cb.SHOW_MEETING.with_id(3)),
        ButtonConfig(text="action_button4", callback_data=cb.SHOW_MEETING.with_id(4)),
    ],
    page_number=2,
    column_size=1,
    row_size=1,
    navigation_callback_data=cb.SHOW_ACTIVE_MEETING_PAGE,
)

PAGINATED_VIEW_LAST = PaginatedMitupView(
    description="last",
    buttons=[
        ButtonConfig(text="action_button1", callback_data=cb.SHOW_MEETING.with_id(1)),
        ButtonConfig(text="action_button2", callback_data=cb.SHOW_MEETING.with_id(2)),
        ButtonConfig(text="action_button3", callback_data=cb.SHOW_MEETING.with_id(3)),
        ButtonConfig(text="action_button4", callback_data=cb.SHOW_MEETING.with_id(4)),
    ],
    page_number=2,
    column_size=1,
    row_size=2,
    navigation_callback_data=cb.SHOW_ACTIVE_MEETING_PAGE,
)

EXPEXTED_MARKUP_UNIQUE = InlineKeyboardMarkup(
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


EXPEXTED_MARKUP_FIRST = InlineKeyboardMarkup(
    [
        [
            InlineKeyboardButton("action_button1", callback_data="show;meeting:1"),
            InlineKeyboardButton("action_button2", callback_data="show;meeting:2"),
            InlineKeyboardButton("action_button3", callback_data="show;meeting:3"),
        ],
        [InlineKeyboardButton(ButtonMessages.GO_FORWARD, callback_data="show;active_meeting_page:2")],
    ]
)

EXPEXTED_MARKUP_MIDDLE = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("action_button2", callback_data="show;meeting:2")],
        [
            InlineKeyboardButton(ButtonMessages.GO_BACK, callback_data="show;active_meeting_page:1"),
            InlineKeyboardButton(ButtonMessages.GO_FORWARD, callback_data="show;active_meeting_page:3"),
        ],
    ]
)

EXPEXTED_MARKUP_LAST = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("action_button3", callback_data="show;meeting:3")],
        [InlineKeyboardButton("action_button4", callback_data="show;meeting:4")],
        [InlineKeyboardButton(ButtonMessages.GO_BACK, callback_data="show;active_meeting_page:1")],
    ]
)


@pytest.mark.parametrize(
    "view, expected_markup",
    [
        (PAGINATED_VIEW_UNIQUE, EXPEXTED_MARKUP_UNIQUE),
        (PAGINATED_VIEW_FIRST, EXPEXTED_MARKUP_FIRST),
        (PAGINATED_VIEW_MIDDLE, EXPEXTED_MARKUP_MIDDLE),
        (PAGINATED_VIEW_LAST, EXPEXTED_MARKUP_LAST),
    ],
    ids=["unique", "first", "middle", "last"],
)
def test_paginated_mitup_view_with_correct_view(view: PaginatedMitupView, expected_markup: InlineKeyboardMarkup):
    assert view.markup == expected_markup


@pytest.mark.parametrize("invalid_page", [0, 2, 6], ids=["invalid_page_0", "invalid_page_3", "invalid_page_6"])
def tests_paginated_mitup_view_with_incorrect_page(invalid_page: int):
    with pytest.raises(ValueError):
        PaginatedMitupView(
            description="unique",
            buttons=[
                ButtonConfig(text="action_button1", callback_data=cb.SHOW_MEETING.with_id(1)),
                ButtonConfig(text="action_button2", callback_data=cb.SHOW_MEETING.with_id(2)),
                ButtonConfig(text="action_button3", callback_data=cb.SHOW_MEETING.with_id(3)),
                ButtonConfig(text="action_button4", callback_data=cb.SHOW_MEETING.with_id(4)),
            ],
            page_number=invalid_page,
            column_size=2,
            row_size=2,
            navigation_callback_data=cb.SHOW_ACTIVE_MEETING_PAGE,
        )


@pytest.mark.parametrize(
    "page_number, item_count, expected",
    [
        (1, 4, 1),  # in range, single page
        (2, 8, 2),  # in range, last page
        (9, 8, 2),  # stale page beyond the end clamps to the last page
        (3, 0, 1),  # empty list clamps to the first page
        (0, 8, 1),  # below range clamps to the first page
    ],
    ids=["in_range_single", "in_range_last", "beyond_end", "empty_list", "below_range"],
)
def test_clamp_page(page_number: int, item_count: int, expected: int):
    assert PaginatedMitupView.clamp_page(page_number, item_count) == expected


def test_paginated_view_fails_without_navigation_callback_and_multiple_pages():
    with pytest.raises(ValueError):
        PaginatedMitupView(
            description="unique",
            buttons=[
                ButtonConfig(text="action_button1", callback_data=cb.SHOW_MEETING.with_id(1)),
                ButtonConfig(text="action_button2", callback_data=cb.SHOW_MEETING.with_id(2)),
                ButtonConfig(text="action_button3", callback_data=cb.SHOW_MEETING.with_id(3)),
                ButtonConfig(text="action_button4", callback_data=cb.SHOW_MEETING.with_id(4)),
            ],
            page_number=1,
            column_size=2,
            row_size=1,
        )
