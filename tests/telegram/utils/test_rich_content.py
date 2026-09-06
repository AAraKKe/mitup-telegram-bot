import datetime as dt

import pytest

from mitup_bot.keyboards import ButtonConfig
from mitup_bot.utils.entities import EntityDateTime
from mitup_bot.utils.rich_message import (
    HORIZONTAL_RULE_MARKUP,
    MAX_RICH_TEXT_LENGTH,
    RichContent,
    RichMessagePayload,
    RichMessageTooLong,
    RichTag,
    as_rich_content,
    button_content,
    button_row_content,
    date_time_content,
    horizontal_rule_content,
    keyboard_content,
    rich_text_length,
    table_content,
)

MOMENT = dt.datetime(2026, 3, 1, 12, 0, tzinfo=dt.UTC)
MOMENT_UNIX = 1772366400


def button(text: str = "Go") -> ButtonConfig:
    return ButtonConfig(text=text, callback_data="do;it")


# ---------------------------------------------------------------------------
# The escaping constructor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("just words", "just words"),
        ("a & b", "a &amp; b"),
        ("5 < 6", "5 &lt; 6"),
        ("7 > 6", "7 &gt; 6"),
        ("<b>user typed this</b>", "&lt;b&gt;user typed this&lt;/b&gt;"),
        ("a &amp; b", "a &amp;amp; b"),
        ('<tg-button type="url" url="x">hi</tg-button>', '&lt;tg-button type="url" url="x"&gt;hi&lt;/tg-button&gt;'),
    ],
    ids=[
        "plain",
        "ampersand",
        "less-than",
        "greater-than",
        "bold-lookalike",
        "character-reference",
        "button-lookalike",
    ],
)
def test_text_entering_through_the_constructor_cannot_become_markup(text: str, expected: str):
    assert RichContent(text).html == expected


def test_text_keeps_quotes_verbatim():
    assert RichContent('she said "it\'s fine"').html == 'she said "it\'s fine"'


def test_newlines_become_line_breaks():
    assert RichContent("first\nsecond").html == "first<br/>second"


def test_newlines_inside_a_pre_block_stay_raw():
    assert RichContent("first\nsecond", inside_pre=True).html == "first\nsecond"


def test_pre_constructor_wraps_the_block_and_keeps_its_newlines():
    assert RichContent.pre("one\ntwo").html == "<pre>one\ntwo</pre>"


def test_empty_content_is_falsy_and_renders_nothing():
    empty = RichContent()

    assert not empty
    assert empty.html == ""


def test_content_with_markup_is_truthy():
    assert RichContent("x")


def test_repr_names_the_type_and_shows_the_markup():
    assert repr(RichContent("a < b")) == "RichContent('a &lt; b')"


def test_equality_compares_the_rendered_markup():
    assert RichContent("a & b") == RichContent("a & b")
    assert RichContent("a") != RichContent("b")


def test_equality_against_another_type_is_not_implemented():
    assert RichContent("a") != "a"


# ---------------------------------------------------------------------------
# Content a plain-text surface can carry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        RichContent(),
        RichContent("plain words"),
        # Escaping and line breaks are how the dialect writes text down, not formatting on it.
        RichContent("a < b & <br/> c"),
        RichContent("two\nlines"),
    ],
    ids=["empty", "words", "escaped_characters", "newline"],
)
def test_content_built_out_of_text_alone_is_plain(content: RichContent):
    assert content.is_plain


@pytest.mark.parametrize(
    "content",
    [
        RichContent("x").wrap(RichTag.BOLD),
        RichContent.link("here", "https://mitup.social"),
        RichContent.pre("x"),
        button_content(button()),
        RichContent("body").append(horizontal_rule_content()),
        date_time_content(EntityDateTime("then", MOMENT)),
    ],
    ids=["bold", "link", "pre", "button", "rule", "moment"],
)
def test_content_carrying_anything_a_reader_would_lose_is_not_plain(content: RichContent):
    assert not content.is_plain


# ---------------------------------------------------------------------------
# Composition never escapes twice
# ---------------------------------------------------------------------------


def test_appending_content_keeps_both_sides_escaped_exactly_once():
    composed = RichContent("a < b").append(RichContent(" & c"))

    assert composed.html == "a &lt; b &amp; c"


def test_appending_a_bare_string_escapes_only_that_string():
    composed = RichContent("x").wrap(RichTag.BOLD).append("a < b")

    assert composed.html == "<b>x</b>a &lt; b"


def test_prepending_a_bare_string_escapes_only_that_string():
    composed = RichContent("x").wrap(RichTag.BOLD).prepend("a < b")

    assert composed.html == "a &lt; b<b>x</b>"


def test_wrapping_does_not_re_escape_the_markup_it_already_holds():
    inner = RichContent("a < b").wrap(RichTag.ITALIC)

    assert inner.wrap(RichTag.BOLD).html == "<b><i>a &lt; b</i></b>"


def test_wrapping_empty_content_adds_no_tags():
    assert RichContent("").wrap(RichTag.BOLD).html == ""


@pytest.mark.parametrize(
    "tag, expected",
    [
        (RichTag.BOLD, "<b>x</b>"),
        (RichTag.ITALIC, "<i>x</i>"),
        (RichTag.UNDERLINE, "<u>x</u>"),
        (RichTag.STRIKETHROUGH, "<s>x</s>"),
        (RichTag.CODE, "<code>x</code>"),
        (RichTag.SPOILER, "<tg-spoiler>x</tg-spoiler>"),
        (RichTag.BLOCKQUOTE, "<blockquote>x</blockquote>"),
        (RichTag.PRE, "<pre>x</pre>"),
    ],
    ids=["bold", "italic", "underline", "strikethrough", "code", "spoiler", "blockquote", "pre"],
)
def test_wrap_emits_the_rich_tag(tag: RichTag, expected: str):
    assert RichContent("x").wrap(tag).html == expected


def test_join_separates_parts_without_escaping_their_markup_again():
    parts = [RichContent("a < b").wrap(RichTag.BOLD), RichContent("c & d")]

    assert RichContent.join(", ", parts).html == "<b>a &lt; b</b>, c &amp; d"


def test_join_escapes_a_bare_string_separator():
    assert RichContent.join(" < ", ["a", "b"]).html == "a &lt; b"


def test_join_of_nothing_is_empty():
    assert RichContent.join(", ", []) == RichContent()


def test_link_escapes_its_label_as_text_and_its_url_as_an_attribute():
    linked = RichContent.link("a < b", "https://x.io/?a=1&b=2")

    assert linked.html == '<a href="https://x.io/?a=1&amp;b=2">a &lt; b</a>'


def test_link_url_cannot_break_out_of_its_quotes():
    linked = RichContent.link("here", 'https://x.io/"onmouseover=')

    assert linked.html == '<a href="https://x.io/&quot;onmouseover=">here</a>'


def test_from_markup_adopts_finished_markup_untouched():
    assert RichContent.from_markup("<b>x</b>").html == "<b>x</b>"


@pytest.mark.parametrize(
    "value, expected",
    [(RichContent("<b>"), "&lt;b&gt;"), ("<b>", "&lt;b&gt;"), (RichContent.from_markup("<b>x</b>"), "<b>x</b>")],
    ids=["content", "string", "markup"],
)
def test_as_rich_content_escapes_only_bare_strings(value: RichContent | str, expected: str):
    assert as_rich_content(value).html == expected


# ---------------------------------------------------------------------------
# Buttons and moments as content
# ---------------------------------------------------------------------------


def test_button_content_serializes_one_button():
    assert button_content(button()).html == '<tg-button type="callback_data" data="do;it">Go</tg-button>'


def test_button_label_is_escaped_as_text():
    assert "&lt;b&gt;" in button_content(button("<b>bold</b>")).html


def test_button_row_content_wraps_its_buttons_in_a_row():
    row = button_row_content([button("A"), button("B")])

    assert row.html.startswith("<tg-button-row>")
    assert row.html.endswith("</tg-button-row>")
    assert row.html.count("<tg-button ") == 2


def test_keyboard_content_emits_one_row_per_keyboard_row():
    assert keyboard_content([[button("A")], [button("B")]]).html.count("<tg-button-row>") == 2


def test_keyboard_content_of_no_rows_is_empty():
    assert keyboard_content([]) == RichContent()


def test_date_time_content_carries_the_moment_and_its_format():
    entity_datetime = EntityDateTime("Sun 1 Mar, 12:00 UTC", MOMENT, "DT")

    assert date_time_content(entity_datetime).html == (
        f'<tg-time unix="{MOMENT_UNIX}" format="DT">Sun 1 Mar, 12:00 UTC</tg-time>'
    )


def test_date_time_content_without_a_format_omits_the_attribute():
    assert date_time_content(EntityDateTime("then", MOMENT)).html == f'<tg-time unix="{MOMENT_UNIX}">then</tg-time>'


def test_date_time_text_is_escaped_as_content():
    assert date_time_content(EntityDateTime("a < b", MOMENT)).html.endswith(">a &lt; b</tg-time>")


def test_date_time_format_is_escaped_as_an_attribute():
    assert 'format="D&quot;T"' in date_time_content(EntityDateTime("then", MOMENT, 'D"T')).html


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


def test_table_content_heads_its_rows_with_the_headings():
    table = table_content(
        [RichContent(), RichContent("Badge")],
        [[RichContent("Brewer"), RichContent("yes")], [RichContent("Gamemaster"), RichContent("yes")]],
    )

    assert table.html == (
        "<table><tr><th></th><th>Badge</th></tr>"
        "<tr><td>Brewer</td><td>yes</td></tr>"
        "<tr><td>Gamemaster</td><td>yes</td></tr></table>"
    )


def test_table_cells_keep_the_markup_they_carry():
    table = table_content([RichContent("Tier")], [[RichContent("Brewer").wrap(RichTag.BOLD)]])

    assert "<td><b>Brewer</b></td>" in table.html


def test_table_projects_one_row_per_line_with_its_cells_spaced_apart():
    table = table_content(
        [RichContent("Tier"), RichContent("Limits")],
        [[RichContent("Brewer"), RichContent("free")], [RichContent("Commissioner"), RichContent("none")]],
    )

    assert table.text == "Tier Limits\nBrewer free\nCommissioner none\n"


# ---------------------------------------------------------------------------
# Measuring what a reader sees
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "markup, expected",
    [
        ("", 0),
        ("hello", 5),
        ("<b>hi</b>", 2),
        ("<b><i>hi</i></b>", 2),
        ("a<br/>b", 3),
        ("a &amp; b", 5),
        ("a &lt;b&gt; c", 7),
        ('<tg-button type="callback_data" data="do;it">Go</tg-button>', 2),
        ('<a href="https://x.io/?a=1&amp;b=2">here</a>', 4),
    ],
    ids=[
        "empty",
        "plain",
        "one-tag",
        "nested",
        "line-break",
        "character-reference",
        "escaped-markup",
        "button",
        "anchor",
    ],
)
def test_rich_text_length_counts_only_what_a_reader_sees(markup: str, expected: int):
    assert rich_text_length(markup) == expected


def test_text_length_ignores_the_markup_carrying_it():
    """A card can spend far more characters on button markup than on the words it shows."""
    content = RichContent("Go").wrap(RichTag.BOLD).append(button_content(button()))

    assert content.text_length == 4
    assert len(content.html) > 50


def test_text_length_of_escaped_user_text_counts_the_original_characters():
    assert RichContent("a < b & c").text_length == 9


# ---------------------------------------------------------------------------
# The payload
# ---------------------------------------------------------------------------


def test_payload_within_the_ceiling_passes_its_length_check():
    RichMessagePayload.from_content(RichContent("a" * 100)).check_length()


def test_payload_over_the_ceiling_raises_naming_its_length():
    payload = RichMessagePayload.from_content(RichContent("a" * (MAX_RICH_TEXT_LENGTH + 1)))

    with pytest.raises(RichMessageTooLong, match=str(MAX_RICH_TEXT_LENGTH + 1)):
        payload.check_length()


def test_payload_exactly_at_the_ceiling_is_accepted():
    RichMessagePayload.from_content(RichContent("a" * MAX_RICH_TEXT_LENGTH)).check_length()


def test_markup_does_not_count_towards_the_ceiling():
    """Button markup is many characters of html per character of visible label."""
    rows = [[button(f"b{index}")] for index in range(400)]
    payload = RichMessagePayload.from_content(RichContent("short body"), rows)

    assert len(payload.html) > MAX_RICH_TEXT_LENGTH
    payload.check_length()


def test_payload_from_content_carries_its_markup():
    payload = RichMessagePayload.from_content(RichContent("a < b"))

    assert payload.html == "a &lt; b"
    assert payload.skip_entity_detection is True


def test_payload_from_content_closes_the_body_with_the_keyboard():
    payload = RichMessagePayload.from_content(RichContent("body"), [[button("A")], [button("B")]])

    assert payload.html.startswith("body<hr/><tg-button-row>")
    assert payload.html.count("<tg-button-row>") == 2


def test_payload_from_content_rules_off_the_keyboard_from_the_body():
    """An in-content keyboard sits in the same flow as the text, so a line separates the two."""
    payload = RichMessagePayload.from_content(RichContent("body"), [[button("A")]])

    assert payload.html.count(HORIZONTAL_RULE_MARKUP) == 1


def test_payload_from_content_without_a_keyboard_adds_no_rows():
    html = RichMessagePayload.from_content(RichContent("body")).html

    assert "tg-button-row" not in html
    # Nothing to separate from the body, so the message ends where its text does.
    assert HORIZONTAL_RULE_MARKUP not in html


def test_payload_from_content_draws_no_rule_for_a_keyboard_of_empty_rows():
    """An empty row is dropped rather than sent, so a keyboard of them closes nothing."""
    html = RichMessagePayload.from_content(RichContent("body"), [[], []]).html

    assert html == "body"


def test_payload_from_content_draws_no_rule_when_the_body_is_the_keyboard():
    html = RichMessagePayload.from_content(RichContent(""), [[button("A")]]).html

    assert html.startswith("<tg-button-row>")
    assert HORIZONTAL_RULE_MARKUP not in html


def test_an_inline_addressed_payload_keeps_its_keyboard_out_of_the_content():
    """The classic-keyboard path carries no rows in the html, so it has nothing to rule off."""
    payload = RichMessagePayload.from_content(RichContent("body"), [[button("A")]], inline_addressed=True)

    assert payload.html == "body"
    assert payload.reply_markup is not None


def test_payload_from_content_reaches_the_api_as_html():
    payload = RichMessagePayload.from_content(RichContent("body"))

    assert payload.to_api_dict() == {"html": "body", "skip_entity_detection": True}
