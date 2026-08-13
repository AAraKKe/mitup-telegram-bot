import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity

import mitup_bot.utils.callbacks as cb
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.utils.entities import MAX_MESSAGE_UTF16_LENGTH, FormattedText, utf16_len
from mitup_bot.views import MitupView, ViewDocument
from mitup_bot.views.mitup_view import (
    CONTEXT_SEPARATOR,
    MitupInlineView,
    PaginatedMitupView,
    PaginatedViewPosition,
)


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


def test_mitup_view_eq_compares_the_document():
    document = ViewDocument(content=b"{}", filename="export.json")

    assert MitupView("hello", keyboard=[], document=document) == MitupView("hello", keyboard=[], document=document)
    assert MitupView("hello", keyboard=[], document=document) != MitupView("hello", keyboard=[])


def test_mitup_view_repr_contains_the_document():
    view = MitupView("hello", keyboard=[], document=ViewDocument(content=b"{}", filename="export.json"))
    assert "export.json" in repr(view)


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


# ---------------------------------------------------------------------------
# with_context — budgeting the context against the message cap
# ---------------------------------------------------------------------------


def test_with_context_under_the_cap_prepends_the_context_unchanged():
    """Regression pin: a context that fits is prepended exactly as before, entities and all."""
    description = FormattedText("Meeting card", [MessageEntity(type=MessageEntity.BOLD, offset=0, length=7)])
    context = FormattedText("Description updated", [MessageEntity(type=MessageEntity.ITALIC, offset=0, length=11)])

    view = MitupView(description, keyboard=[]).with_context(context)

    assert view.description == FormattedText(
        "Description updated\n\nMeeting card",
        [
            MessageEntity(type=MessageEntity.ITALIC, offset=0, length=11),
            MessageEntity(type=MessageEntity.BOLD, offset=21, length=7),
        ],
    )


def test_with_context_ellipsizes_the_context_to_the_room_the_description_leaves():
    """The card is the content and the context is a transient echo of it, so the echo is what is
    cut — and the entities on both sides stay inside the text Telegram is handed."""
    card = FormattedText(
        "D" * (MAX_MESSAGE_UTF16_LENGTH - 104) + "TAIL",
        [MessageEntity(type=MessageEntity.BOLD, offset=0, length=4)],
    )
    echo = FormattedText("C" * 500, [MessageEntity(type=MessageEntity.ITALIC, offset=0, length=500)])

    view = MitupView(card, keyboard=[]).with_context(echo)

    room = 98  # the cap, less the card, less the two-newline separator
    assert utf16_len(view.description.text) == MAX_MESSAGE_UTF16_LENGTH
    assert view.description.text.startswith("C" * (room - 1) + "…" + CONTEXT_SEPARATOR)
    # The card survives whole: its tail is the part a cut in the wrong direction would eat.
    assert view.description.text.endswith("TAIL")
    assert view.description.entities == [
        MessageEntity(type=MessageEntity.ITALIC, offset=0, length=room - 1),
        MessageEntity(type=MessageEntity.BOLD, offset=room + utf16_len(CONTEXT_SEPARATOR), length=4),
    ]


@pytest.mark.parametrize(
    "card_length",
    [MAX_MESSAGE_UTF16_LENGTH, MAX_MESSAGE_UTF16_LENGTH - utf16_len(CONTEXT_SEPARATOR)],
    ids=["description_at_the_cap", "description_leaving_only_the_separator"],
)
def test_with_context_drops_the_context_when_the_description_leaves_no_room(card_length: int):
    """With no room the context collapses to nothing — the separator alone would still overflow."""
    card = FormattedText("D" * card_length)

    view = MitupView(card, keyboard=[]).with_context("Description updated")

    assert view.description == card


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
