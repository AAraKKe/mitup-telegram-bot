from typing import Any

import pytest

import mitup_bot.utils.callbacks as cb
from mitup_bot.keyboards import ButtonConfig, ButtonRow, Keyboard
from mitup_bot.utils import ButtonMessages
from mitup_bot.utils.rich_message import RichContent
from mitup_bot.views import PaginatedMitupView


def meeting_button(index: int) -> ButtonConfig:
    return ButtonConfig(text=f"action_button{index}", callback_data=cb.SHOW_MEETING.with_id(index))


def page_button(text: ButtonMessages, page_number: int) -> ButtonConfig:
    return ButtonConfig(text=text, callback_data=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(page_number))


PAGINATED_VIEW_UNIQUE = PaginatedMitupView(
    message=RichContent("unique"),
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
    message=RichContent("first"),
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
    message=RichContent("middle"),
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
    message=RichContent("last"),
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

EXPECTED_MENU_UNIQUE = [
    [meeting_button(1), meeting_button(2)],
    [meeting_button(3), meeting_button(4)],
]

EXPECTED_MENU_FIRST = [
    [meeting_button(1), meeting_button(2), meeting_button(3)],
    [page_button(ButtonMessages.GO_FORWARD, 2)],
]

EXPECTED_MENU_MIDDLE = [
    [meeting_button(2)],
    [page_button(ButtonMessages.GO_BACK, 1), page_button(ButtonMessages.GO_FORWARD, 3)],
]

EXPECTED_MENU_LAST = [
    [meeting_button(3)],
    [meeting_button(4)],
    [page_button(ButtonMessages.GO_BACK, 1)],
]


@pytest.mark.parametrize(
    "view, expected_menu",
    [
        (PAGINATED_VIEW_UNIQUE, EXPECTED_MENU_UNIQUE),
        (PAGINATED_VIEW_FIRST, EXPECTED_MENU_FIRST),
        (PAGINATED_VIEW_MIDDLE, EXPECTED_MENU_MIDDLE),
        (PAGINATED_VIEW_LAST, EXPECTED_MENU_LAST),
    ],
    ids=["unique", "first", "middle", "last"],
)
def test_paginated_mitup_view_with_correct_view(view: PaginatedMitupView, expected_menu: Keyboard):
    assert view.menu == expected_menu


@pytest.mark.parametrize("invalid_page", [0, 2, 6], ids=["invalid_page_0", "invalid_page_3", "invalid_page_6"])
def tests_paginated_mitup_view_with_incorrect_page(invalid_page: int):
    with pytest.raises(ValueError):
        PaginatedMitupView(
            message=RichContent("unique"),
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
            message=RichContent("unique"),
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


def meeting_section(index: int) -> RichContent:
    return RichContent(f"section {index}")


def sections_view(page_number: int, *, count: int = 7, page_size: int = 3) -> PaginatedMitupView:
    return PaginatedMitupView(
        message=RichContent("heading"),
        sections=[meeting_section(index) for index in range(1, count + 1)],
        page_size=page_size,
        page_number=page_number,
        navigation_callback_data=cb.SHOW_ACTIVE_MEETING_PAGE,
    )


@pytest.mark.parametrize(
    "page_number, expected_body",
    [
        (1, "heading<hr/>section 1<hr/>section 2<hr/>section 3"),
        (2, "heading<hr/>section 4<hr/>section 5<hr/>section 6"),
        (3, "heading<hr/>section 7"),
    ],
    ids=["first", "middle", "last"],
)
def test_a_sections_page_carries_its_own_slice_under_the_message(page_number: int, expected_body: str):
    assert sections_view(page_number).message.html == expected_body


def test_a_single_page_of_sections_needs_no_navigation():
    view = PaginatedMitupView(message=RichContent("heading"), sections=[meeting_section(1)], page_size=3, page_number=1)

    assert view.menu == []


@pytest.mark.parametrize(
    "page_number, expected_row",
    [
        (1, [page_button(ButtonMessages.GO_FORWARD, 2)]),
        (2, [page_button(ButtonMessages.GO_BACK, 1), page_button(ButtonMessages.GO_FORWARD, 3)]),
        (3, [page_button(ButtonMessages.GO_BACK, 2)]),
    ],
    ids=["first", "middle", "last"],
)
def test_the_arrows_close_a_sections_page_as_they_close_a_buttons_page(page_number: int, expected_row: ButtonRow):
    assert sections_view(page_number).menu == [expected_row]


def test_a_sections_page_beyond_the_last_one_is_refused():
    with pytest.raises(ValueError):
        sections_view(4)


def test_sections_spanning_several_pages_need_a_navigation_callback():
    with pytest.raises(ValueError):
        PaginatedMitupView(
            message=RichContent("heading"),
            sections=[meeting_section(index) for index in range(1, 5)],
            page_size=3,
            page_number=1,
        )


def test_clamp_page_follows_the_page_size_it_is_given():
    assert PaginatedMitupView.clamp_page(9, 7, 3) == 3


def paginated_view(**arguments: Any) -> PaginatedMitupView:
    """Build a view from arguments the overloads forbid, so the runtime refusal can be pinned."""
    return PaginatedMitupView(**arguments)


@pytest.mark.parametrize(
    "arguments",
    [
        {"buttons": [meeting_button(1)], "sections": [meeting_section(1)], "page_size": 3},
        {},
        {"sections": [meeting_section(1)], "page_size": 3, "column_size": 2},
        {"buttons": [meeting_button(1)], "page_size": 3},
    ],
    ids=["both_kinds", "neither_kind", "sections_in_a_grid", "buttons_by_page_size"],
)
def test_a_page_is_sized_by_the_arguments_of_the_kind_it_holds(arguments: dict[str, Any]):
    with pytest.raises(ValueError):
        paginated_view(message=RichContent("heading"), page_number=1, **arguments)
