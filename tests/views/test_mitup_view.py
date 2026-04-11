from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import mitup_bot.utils.callbacks as cb
from mitup_bot.utils.entities import FormattedText
from mitup_bot.views import ButtonConfig, MitupView
from mitup_bot.views.mitup_view import MitupInlineView, PaginatedMitupView, PaginatedViewPosition


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


def test_button_config_validate_text_passes_formatted_text_without_entities_through():
    # A FormattedText with no entities should be returned as-is (line 42 branch in validate_text)
    ft = FormattedText("plain text")
    btn = ButtonConfig(text=ft, callback_data=cb.SHOW_MEETING.with_id(1))
    # The validated value stored on the model must be the same FormattedText object
    assert btn.text == ft


def test_button_config_validate_text_raises_when_formatted_text_has_entities():
    import pytest
    from pydantic import ValidationError
    from telegram import MessageEntity

    # A FormattedText with at least one entity must be rejected by the validator
    ft = FormattedText("bold", [MessageEntity(type="bold", offset=0, length=4)])

    with pytest.raises(ValidationError, match="ButtonConfig text should not contain entities"):
        ButtonConfig(text=ft, callback_data=cb.SHOW_MEETING.with_id(1))


def test_mitup_view_eq_returns_not_implemented_for_non_view():
    view = MitupView("hello", keyboard=[])
    result = view.__eq__("not a view")
    # __eq__ must signal the comparison cannot be made, not return False
    assert result is NotImplemented


def test_mitup_view_repr_contains_description():
    view = MitupView("hello world", keyboard=[])
    r = repr(view)
    # repr must identify the class and include the description text
    assert "MitupView" in r
    assert "hello world" in r


def test_mitup_inline_view_eq_returns_not_implemented_for_non_inline_view():
    inline_view = MitupInlineView(
        description="desc",
        keyboard=[],
        title="title",
        inline_description="short",
        id="abc",
    )
    plain_view = MitupView("desc", keyboard=[])
    result = inline_view.__eq__(plain_view)
    # Comparing MitupInlineView to a plain MitupView must return NotImplemented
    assert result is NotImplemented


def test_paginated_view_unique_position_navigation_row_is_empty():
    # With exactly page_size (4) buttons total_pages == 1, position is UNIQUE
    buttons = [ButtonConfig(text=str(i), callback_data=cb.SHOW_MEETING.with_id(i)) for i in range(1, 5)]
    view = PaginatedMitupView(
        description="test",
        buttons=buttons,
        page_number=1,
        row_size=2,
        column_size=2,
    )
    assert view.position is PaginatedViewPosition.UNIQUE
    # The UNIQUE branch in __match_navigation_button returns [] so no navigation row is appended
    # The keyboard only contains the button rows (1 row of 2 + 1 row of 2 = 2 rows total)
    assert len(view.keyboard) == 2  # two rows of 2 buttons, no navigation row


def test_paginated_view_match_navigation_button_returns_empty_list_for_unique_position():
    """The UNIQUE case in __match_navigation_button (lines 262-263) returns [].

    __get_paginated_view skips calling __match_navigation_button when position is UNIQUE,
    so the only way to cover this branch is to invoke the method directly via name mangling."""
    buttons = [ButtonConfig(text=str(i), callback_data=cb.SHOW_MEETING.with_id(i)) for i in range(1, 5)]
    view = PaginatedMitupView(
        description="test",
        buttons=buttons,
        page_number=1,
        row_size=2,
        column_size=2,
    )
    assert view.position is PaginatedViewPosition.UNIQUE

    # Call the private method directly — returns [] for UNIQUE (line 262-263)
    result = view._PaginatedMitupView__match_navigation_button(cb.SHOW_MEETING)  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]  # https://github.com/astral-sh/ty/issues/645
    assert result == []  # UNIQUE case returns an empty list, not navigation buttons
