from telegram import MessageEntity

import mitup_bot.utils.callbacks as cb
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.utils.rich_message import RichContent, RichDocument, RichPhoto, collage_content, photo_content
from mitup_bot.views import MitupView
from mitup_bot.views.mitup_view import MitupInlineView, PaginatedMitupView


def test_mitup_view_eq_returns_not_implemented_for_non_view():
    view = MitupView(RichContent("hello"), menu=[])
    result = view.__eq__("not a view")
    # __eq__ must signal the comparison cannot be made, not return False
    assert result is NotImplemented


def test_mitup_view_repr_contains_description():
    view = MitupView(RichContent("hello world"), menu=[])
    r = repr(view)
    # repr must identify the class and include the description text
    assert "MitupView" in r
    assert "hello world" in r


def test_mitup_view_eq_compares_the_document():
    document = RichDocument(content=b"{}", filename="export.json")

    assert MitupView(RichContent("hello"), menu=[], document=document) == MitupView(
        RichContent("hello"), menu=[], document=document
    )
    assert MitupView(RichContent("hello"), menu=[], document=document) != MitupView(RichContent("hello"), menu=[])


def test_mitup_view_repr_contains_the_document():
    view = MitupView(
        RichContent("hello"),
        menu=[],
        document=RichDocument(content=b"{}", filename="export.json"),
    )
    assert "export.json" in repr(view)


def test_mitup_inline_view_eq_returns_not_implemented_for_non_inline_view():
    inline_view = MitupInlineView(
        message=RichContent("desc"),
        menu=[],
        title="title",
        inline_description="short",
        id="abc",
    )
    plain_view = MitupView(RichContent("desc"), menu=[])
    result = inline_view.__eq__(plain_view)
    # Comparing MitupInlineView to a plain MitupView must return NotImplemented
    assert result is NotImplemented


# ---------------------------------------------------------------------------
# with_context — budgeting the context against the message cap
# ---------------------------------------------------------------------------


def test_with_context_prepends_the_context_above_what_it_comments_on():
    view = MitupView(RichContent.from_markup("<b>Meeting</b> card"), menu=[]).with_context(
        RichContent.from_markup("<i>Description updated</i>")
    )

    assert view.message.html == "<i>Description updated</i><hr/><b>Meeting</b> card"


def test_with_context_escapes_a_bare_string():
    view = MitupView(RichContent("card"), menu=[]).with_context("a < b")

    assert view.message.html == "a &lt; b<hr/>card"


def test_with_context_never_trims_the_context_to_fit():
    """Length is a wire limit checked on the finished payload, not a reading problem solved by
    cutting: a client folds a long message behind "Show more"."""
    echo = "C" * 5000

    view = MitupView(RichContent("card"), menu=[]).with_context(RichContent(echo))

    assert view.message.html.startswith(echo)
    assert view.message.html.endswith("card")


def test_with_context_keeps_a_long_body_whole():
    body = "D" * 5000

    view = MitupView(RichContent(body), menu=[]).with_context("note")

    assert view.message.text == "note\n" + body


def test_paginated_view_single_page_appends_no_navigation_row():
    # With exactly page_size (4) buttons there is a single page, so the keyboard is the grid alone.
    buttons = [ButtonConfig(text=str(i), callback_data=cb.SHOW_MEETING.with_id(i)) for i in range(1, 5)]
    view = PaginatedMitupView(
        message=RichContent("test"),
        buttons=buttons,
        page_number=1,
        row_size=2,
        column_size=2,
    )
    assert len(view.menu) == 2  # two rows of 2 buttons, no navigation row


# ---------------------------------------------------------------------------
# The rich message a view is sent as
# ---------------------------------------------------------------------------

CUSTOM_EMOJI = MessageEntity(type=MessageEntity.CUSTOM_EMOJI, offset=0, length=2, custom_emoji_id="123456")
BOLD = MessageEntity(type=MessageEntity.BOLD, offset=3, length=4)


def test_rich_message_serializes_the_description_into_html():
    view = MitupView(RichContent.from_markup("<b>" + "hi there"[:2] + "</b>" + "hi there"[2:]), [])

    assert view.rich_message().to_api_dict() == {"html": "<b>hi</b> there", "skip_entity_detection": True}


def test_rich_message_closes_the_content_with_the_keyboard():
    """A rich message carries its buttons inside its content, so the payload the view renders is
    the whole message and nothing travels beside it."""
    view = MitupView(RichContent("press it"), [[ButtonConfig(text="Go", callback_data=cb.SHOW_MEETING.with_id(1))]])

    assert view.rich_message().html == (
        'press it<hr/><tg-button-row><tg-button type="callback_data" data="show;meeting:1">Go</tg-button>'
        "</tg-button-row>"
    )


def test_rich_message_puts_the_carried_file_between_the_body_and_the_buttons():
    view = MitupView(
        RichContent("your data"),
        [[ButtonConfig(text="Go", callback_data=cb.SHOW_MEETING.with_id(1))]],
        document=RichDocument(content=b"{}", filename="export.json"),
    )

    payload = view.rich_message()

    assert payload.html == (
        'your data<tg-document src="tg://document?id=document"></tg-document>'
        '<hr/><tg-button-row><tg-button type="callback_data" data="show;meeting:1">Go</tg-button></tg-button-row>'
    )
    # The payload keeps the file itself, which is what the send uploads beside the content.
    assert payload.document == view.document


BANNER = RichPhoto(media_id="AQADHRJrGzSd4FB-", file_id="AgACAgQAAxkBAAIB")


def banner_view() -> MitupView:
    return MitupView(
        RichContent("the meeting").prepend(collage_content([photo_content(BANNER.media_id)])),
        [],
        photos=[BANNER],
    )


def test_mitup_view_eq_compares_the_photos():
    assert banner_view() == banner_view()
    assert banner_view() != MitupView(banner_view().message, [], photos=[RichPhoto(media_id="X", file_id="Y")])


def test_mitup_view_repr_contains_the_photos():
    assert "AQADHRJrGzSd4FB-" in repr(banner_view())


def test_rich_message_names_the_photos_the_body_shows():
    payload = banner_view().rich_message()

    assert payload.html.startswith('<tg-collage><img src="tg://photo?id=AQADHRJrGzSd4FB-"/></tg-collage>')
    assert payload.to_api_dict()["media"] == [
        {"id": "AQADHRJrGzSd4FB-", "media": {"type": "photo", "media": "AgACAgQAAxkBAAIB"}}
    ]


def test_an_inline_result_carries_the_photos_it_shows():
    """A picked inline result sends the card as a new message, so the media list has to travel
    inside the input message content."""
    view = MitupInlineView(
        message=RichContent("the meeting").prepend(collage_content([photo_content(BANNER.media_id)])),
        menu=[],
        photos=[BANNER],
        title="Title",
        inline_description="Description",
        id="1",
    )

    result = view.inline_result()

    assert result["input_message_content"]["rich_message"]["media"] == [
        {"id": "AQADHRJrGzSd4FB-", "media": {"type": "photo", "media": "AgACAgQAAxkBAAIB"}}
    ]


def test_rich_message_renders_one_row_per_keyboard_row_in_order():
    view = MitupView(
        RichContent("pick"),
        [
            [ButtonConfig(text="A", callback_data=cb.SHOW_MEETING.with_id(1))],
            [
                ButtonConfig(text="B", callback_data=cb.SHOW_MEETING.with_id(2)),
                ButtonConfig(text="C", url="https://mitup.social"),
            ],
        ],
    )

    assert view.rich_message().html == (
        "pick<hr/>"
        '<tg-button-row><tg-button type="callback_data" data="show;meeting:1">A</tg-button></tg-button-row>'
        '<tg-button-row><tg-button type="callback_data" data="show;meeting:2">B</tg-button>'
        '<tg-button type="url" url="https://mitup.social">C</tg-button></tg-button-row>'
    )


def test_rich_message_escapes_a_button_label_that_would_read_as_markup():
    view = MitupView(RichContent("body"), [[ButtonConfig(text="5 < 6", callback_data=cb.SHOW_MEETING.with_id(1))]])

    assert "5 &lt; 6" in view.rich_message().html


def test_rich_message_escapes_text_that_would_otherwise_read_as_markup():
    view = MitupView(RichContent("5 < 6 & rising"), [])

    assert view.rich_message().html == "5 &lt; 6 &amp; rising"


def test_rich_message_without_custom_emoji_falls_back_to_the_glyph():
    view = MitupView(RichContent.from_markup('<tg-emoji emoji-id="123456">🎉</tg-emoji> <b>bold</b>'), [])

    assert view.rich_message(without_custom_emoji=True).html == "🎉 <b>bold</b>"


def test_rich_message_keeps_the_custom_emoji_by_default():
    view = MitupView(RichContent.from_markup('<tg-emoji emoji-id="123456">🎉</tg-emoji> <b>bold</b>'), [])

    assert view.rich_message().html == '<tg-emoji emoji-id="123456">🎉</tg-emoji> <b>bold</b>'


def test_rich_message_without_custom_emoji_keeps_the_buttons():
    view = MitupView(
        RichContent.from_markup('<tg-emoji emoji-id="123456">🎉</tg-emoji> <b>bold</b>'),
        [[ButtonConfig(text="Go", callback_data=cb.SHOW_MEETING.with_id(1))]],
    )

    assert view.rich_message(without_custom_emoji=True).html == (
        '🎉 <b>bold</b><hr/><tg-button-row><tg-button type="callback_data" data="show;meeting:1">Go</tg-button>'
        "</tg-button-row>"
    )


def test_carries_custom_emoji_answers_whether_the_retry_has_anything_to_strip():
    assert (
        MitupView(
            RichContent.from_markup('<tg-emoji emoji-id="123456">🎉</tg-emoji> <b>bold</b>'), []
        ).carries_custom_emoji
        is True
    )
    assert MitupView(RichContent.from_markup("🎉 <b>bold</b>"), []).carries_custom_emoji is False
    assert MitupView(RichContent("plain"), []).carries_custom_emoji is False


def test_a_builder_added_context_is_part_of_the_rendered_message():
    """The builders mutate the description in place, so a payload produced after one has to carry
    what it added."""
    view = MitupView(RichContent("the body"), []).with_context(RichContent.from_markup("<b>done</b>"))

    assert view.rich_message().html == "<b>done</b><hr/>the body"


def test_an_inline_view_renders_the_same_way():
    view = MitupInlineView(
        message=RichContent.from_markup("<b>" + "hi there"[:2] + "</b>" + "hi there"[2:]),
        menu=[],
        title="Title",
        inline_description="Description",
        id="1",
    )

    assert view.rich_message().html == "<b>hi</b> there"
