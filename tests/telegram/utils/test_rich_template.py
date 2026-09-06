import datetime as dt
from typing import override

import pytest
from structlog.testing import capture_logs

from mitup_bot.format_tags import ATTRIBUTE_TAG_NAMES, STYLE_TAG_NAMES
from mitup_bot.keyboards import ButtonConfig, ButtonRow, Keyboard
from mitup_bot.translations import TranslationEngine
from mitup_bot.utils.entities import Bold, BoldItalic, EntityDateTime, Italic, Link
from mitup_bot.utils.messages import FormattedMessageAsText, MessageBase, TranslationEngineProtocol
from mitup_bot.utils.rich_message import RichContent, RichTag
from mitup_bot.utils.rich_template import (
    FORMAT_TAG_RICH_TAGS,
    ButtonOutsideButtonPlaceholder,
    MalformedRichFormatTag,
    MissingRichPlaceholder,
    NonButtonInButtonPlaceholder,
    RichParams,
    UnbalancedRichFormatTag,
    UnclosedRichFormatTag,
    UnsupportedRichFormatTag,
    UnsupportedRichInterpolation,
    render_rich,
    render_rich_tags,
    render_rich_template,
)

MOMENT = dt.datetime(2026, 3, 1, 12, 0, tzinfo=dt.UTC)
MOMENT_UNIX = 1772366400

BUTTON_MARKUP = '<tg-button type="callback_data" data="do;it">Go</tg-button>'

STORED_RENDER_EVENT = "Stored tagged text did not render"


def button(text: str = "Go") -> ButtonConfig:
    return ButtonConfig(text=text, callback_data="do;it")


def rendered(source: str, **substitutions: RichParams) -> str:
    return render_rich_template(source, substitutions).html


# ---------------------------------------------------------------------------
# t-strings
# ---------------------------------------------------------------------------


def test_literal_runs_are_escaped_as_content():
    assert render_rich(t"a < b & c").html == "a &lt; b &amp; c"


def test_literal_newlines_become_line_breaks():
    assert render_rich(t"first\n\nsecond").html == "first<br/><br/>second"


def test_interpolated_string_is_escaped():
    name = "<b>bold</b>"

    assert render_rich(t"hi {name}").html == "hi &lt;b&gt;bold&lt;/b&gt;"


def test_interpolated_content_keeps_its_markup():
    emphasis = RichContent("a < b").wrap(RichTag.BOLD)

    assert render_rich(t"see {emphasis}").html == "see <b>a &lt; b</b>"


def test_interpolated_numbers_render_as_their_digits():
    count, ratio = 3, 1.5

    assert render_rich(t"{count} of {ratio}").html == "3 of 1.5"


def test_interpolated_button_becomes_an_inline_chip():
    chip = button()

    assert render_rich(t"tap {chip} now").html == f"tap {BUTTON_MARKUP} now"


def test_interpolated_button_row_becomes_one_row():
    row: ButtonRow = [button("A"), button("B")]

    markup = render_rich(t"{row}").html
    assert markup.count("<tg-button-row>") == 1
    assert markup.count("<tg-button ") == 2


def test_interpolated_keyboard_becomes_a_run_of_rows():
    keyboard: Keyboard = [[button("A")], [button("B")]]

    assert render_rich(t"{keyboard}").html.count("<tg-button-row>") == 2


def test_interpolated_empty_keyboard_renders_nothing():
    keyboard: Keyboard = []

    assert render_rich(t"body{keyboard}").html == "body"


@pytest.mark.parametrize(
    "wrapper, expected",
    [
        (Bold("a < b"), "<b>a &lt; b</b>"),
        (Italic("a < b"), "<i>a &lt; b</i>"),
        (BoldItalic("a < b"), "<b><i>a &lt; b</i></b>"),
        (Link("a < b", "https://x.io/?a=1&b=2"), '<a href="https://x.io/?a=1&amp;b=2">a &lt; b</a>'),
    ],
    ids=["bold", "italic", "bold-italic", "link"],
)
def test_entity_wrapper_becomes_its_rich_tag_with_its_text_escaped(wrapper: RichParams, expected: str):
    assert render_rich(t"{wrapper}").html == expected


def test_entity_wrappers_compose_side_by_side():
    label, target = Bold("Title"), Link("here", "https://x.io")

    assert render_rich(t"{label}: {target}").html == '<b>Title</b>: <a href="https://x.io">here</a>'


def test_a_link_nests_inside_a_bold_through_a_catalog_tag():
    assert rendered("<b>Read ${link}</b>", link=Link("here", "https://x.io")) == (
        '<b>Read <a href="https://x.io">here</a></b>'
    )


def test_a_link_nests_inside_a_bold_through_composition():
    inner = render_rich(t"Read {Link('here', 'https://x.io')}")

    assert inner.wrap(RichTag.BOLD).html == '<b>Read <a href="https://x.io">here</a></b>'


def test_an_italic_wrapper_nests_inside_a_catalog_bold():
    assert rendered("<b>${name}</b>", name=Italic("Alice")) == "<b><i>Alice</i></b>"


def test_a_wrapper_substituted_inside_a_pre_block_keeps_its_newlines():
    assert rendered("<pre>${code}</pre>", code=Bold("one\ntwo")) == "<pre><b>one\ntwo</b></pre>"


def test_a_wrapper_may_not_fill_a_button_placeholder():
    with pytest.raises(NonButtonInButtonPlaceholder, match="Bold"):
        rendered("tap ${button_go}", button_go=Bold("Go"))


def test_interpolated_moment_becomes_a_time_tag():
    when = EntityDateTime("Sun 1 Mar", MOMENT, "DT")

    assert render_rich(t"at {when}").html == f'at <tg-time unix="{MOMENT_UNIX}" format="DT">Sun 1 Mar</tg-time>'


def test_nested_template_renders_into_the_outer_content():
    inner = t"a < b"

    assert render_rich(t"[{inner}]").html == "[a &lt; b]"


@pytest.mark.parametrize(
    "value, type_name",
    [(None, "NoneType"), (True, "bool"), ({"a": 1}, "dict"), ([button(), "x"], "list"), (object(), "object")],
    ids=["none", "bool", "mapping", "mixed-list", "bare-object"],
)
def test_unsupported_interpolation_raises_naming_the_type(value: object, type_name: str):
    with pytest.raises(UnsupportedRichInterpolation, match=type_name):
        render_rich(t"{value}")


# ---------------------------------------------------------------------------
# Catalog strings: substitution
# ---------------------------------------------------------------------------


def test_placeholder_is_replaced_by_its_escaped_value():
    assert rendered("hi ${name}", name="<b>x</b>") == "hi &lt;b&gt;x&lt;/b&gt;"


def test_placeholder_accepts_rich_content_without_escaping_it_again():
    assert rendered("hi ${name}", name=RichContent("x").wrap(RichTag.BOLD)) == "hi <b>x</b>"


def test_placeholder_accepts_a_moment():
    assert (
        rendered("at ${when}", when=EntityDateTime("noon", MOMENT))
        == f'at <tg-time unix="{MOMENT_UNIX}">noon</tg-time>'
    )


def test_missing_placeholder_raises_naming_it():
    with pytest.raises(MissingRichPlaceholder, match=r"\$\{cap\}"):
        rendered("up to ${cap} meetings")


def test_a_literal_placeholder_never_reaches_the_output():
    with pytest.raises(MissingRichPlaceholder):
        rendered("<b>${days}</b> days", other="unused")


def test_unused_substitutions_are_ignored():
    assert rendered("plain", name="unused") == "plain"


def test_repeated_placeholder_is_substituted_every_time():
    assert rendered("${tier} then ${tier}", tier="Brewer") == "Brewer then Brewer"


def test_source_character_references_decode_before_escaping():
    assert rendered("a &amp; b &lt;c&gt;") == "a &amp; b &lt;c&gt;"


def test_source_newlines_become_line_breaks():
    assert rendered("first\n\nsecond") == "first<br/><br/>second"


# ---------------------------------------------------------------------------
# Catalog strings: the button placeholder contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [button(), [button("A"), button("B")], [[button("A")], [button("B")]]],
    ids=["button", "row", "keyboard"],
)
def test_button_placeholder_accepts_every_button_shape(value: RichParams):
    assert "tg-button" in rendered("tap ${button_go}", button_go=value)


def test_button_placeholder_given_text_raises_naming_the_placeholder():
    with pytest.raises(NonButtonInButtonPlaceholder, match=r"\$\{button_go\}"):
        rendered("tap ${button_go}", button_go="Go")


def test_button_placeholder_given_a_number_raises():
    with pytest.raises(NonButtonInButtonPlaceholder, match="int"):
        rendered("tap ${button_go}", button_go=3)


def test_button_outside_a_button_placeholder_raises_naming_the_placeholder():
    with pytest.raises(ButtonOutsideButtonPlaceholder, match=r"\$\{name\}"):
        rendered("tap ${name}", name=button())


def test_a_button_named_placeholder_is_checked_even_when_the_source_omits_it():
    with pytest.raises(NonButtonInButtonPlaceholder):
        rendered("no placeholders here", button_go="Go")


def test_a_label_placeholder_ending_in_button_still_takes_plain_text():
    """The catalogs name label placeholders `${..._button}`; only the `button_` prefix means markup."""
    assert rendered("Click <b>${new_meeting_button}</b>", new_meeting_button="New meeting") == (
        "Click <b>New meeting</b>"
    )


# ---------------------------------------------------------------------------
# Catalog strings: the formatting-tag dialect
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source_tag, rich_tag",
    [
        ("b", "b"),
        ("strong", "b"),
        ("i", "i"),
        ("em", "i"),
        ("u", "u"),
        ("ins", "u"),
        ("s", "s"),
        ("strike", "s"),
        ("del", "s"),
        ("code", "code"),
        ("spoiler", "tg-spoiler"),
        ("tg-spoiler", "tg-spoiler"),
        ("blockquote", "blockquote"),
    ],
    ids=[
        "b",
        "strong",
        "i",
        "em",
        "u",
        "ins",
        "s",
        "strike",
        "del",
        "code",
        "spoiler",
        "tg-spoiler",
        "blockquote",
    ],
)
def test_source_tag_maps_onto_its_rich_tag(source_tag: str, rich_tag: str):
    assert rendered(f"<{source_tag}>x</{source_tag}>") == f"<{rich_tag}>x</{rich_tag}>"


def test_pre_maps_onto_pre_and_keeps_its_raw_newlines():
    assert rendered("<pre>one\ntwo</pre>") == "<pre>one\ntwo</pre>"


def test_newlines_outside_a_pre_block_still_become_line_breaks():
    assert rendered("<pre>one\ntwo</pre>\nafter") == "<pre>one\ntwo</pre><br/>after"


def test_a_value_substituted_inside_a_pre_block_keeps_its_newlines():
    assert rendered("<pre>${code}</pre>", code="one\ntwo") == "<pre>one\ntwo</pre>"


def test_span_spoiler_maps_onto_the_spoiler_tag():
    assert rendered('<span class="tg-spoiler">x</span>') == "<tg-spoiler>x</tg-spoiler>"


def test_anchor_keeps_its_href_escaped_as_an_attribute():
    assert rendered('<a href="https://x.io/?a=1&b=2">here</a>') == '<a href="https://x.io/?a=1&amp;b=2">here</a>'


def test_custom_emoji_keeps_its_id():
    assert rendered('<tg-emoji emoji-id="55">x</tg-emoji>') == '<tg-emoji emoji-id="55">x</tg-emoji>'


def test_tags_nest():
    assert rendered("<b>bold <i>and italic</i></b>") == "<b>bold <i>and italic</i></b>"


def test_tags_nest_around_a_substitution():
    assert rendered("<b><i>${name}</i></b>", name="a < b") == "<b><i>a &lt; b</i></b>"


def test_unmapped_tag_raises_naming_it():
    with pytest.raises(UnsupportedRichFormatTag, match="marquee"):
        rendered("<marquee>x</marquee>")


@pytest.mark.parametrize(
    "source, attribute",
    [
        ('<a href="">x</a>', "href"),
        ('<span class="other">x</span>', "class"),
        ('<tg-emoji emoji-id="">x</tg-emoji>', "emoji-id"),
    ],
    ids=["anchor-without-href", "span-that-is-not-a-spoiler", "emoji-without-id"],
)
def test_attribute_bearing_tag_without_its_attribute_raises(source: str, attribute: str):
    with pytest.raises(MalformedRichFormatTag, match=attribute):
        rendered(source)


def test_crossing_tags_raise_because_they_cannot_nest():
    with pytest.raises(UnbalancedRichFormatTag, match="b"):
        rendered("<b>a<i>b</b>c</i>")


def test_closing_a_tag_that_was_never_opened_raises():
    with pytest.raises(UnbalancedRichFormatTag, match="i"):
        rendered("a</i>")


def test_unclosed_tag_raises_naming_it():
    with pytest.raises(UnclosedRichFormatTag, match="b"):
        rendered("<b>never closed")


def test_the_rich_mapping_covers_exactly_the_dialect_vocabulary():
    """The renderer and `mb locales validate` must admit the same tags, or a translation could
    pass the check and then fail to render."""
    assert set(FORMAT_TAG_RICH_TAGS) == STYLE_TAG_NAMES


@pytest.mark.parametrize("tag", sorted(ATTRIBUTE_TAG_NAMES), ids=sorted(ATTRIBUTE_TAG_NAMES))
def test_every_attribute_bearing_tag_in_the_vocabulary_resolves(tag: str):
    """Each one is resolved by its attributes rather than by the mapping, so the vocabulary is the
    only place recording that the dialect admits it at all."""
    attrs = {"a": ' href="https://x.io"', "span": ' class="tg-spoiler"', "tg-emoji": ' emoji-id="1"'}[tag]

    assert rendered(f"<{tag}{attrs}>x</{tag}>")


# ---------------------------------------------------------------------------
# Stored user content
# ---------------------------------------------------------------------------


def stored(text: str) -> str:
    return render_rich_tags(text, field="description").html


def test_stored_content_keeps_a_literal_placeholder_a_user_typed():
    """`${...}` in a description is text somebody wrote, so reading it as a placeholder would
    break that user's own card."""
    assert stored("tickets are ${cap} each") == "tickets are ${cap} each"


def test_stored_content_with_a_placeholder_never_raises_for_a_missing_value():
    assert stored("${anything} ${at} ${all}") == "${anything} ${at} ${all}"


def test_stored_multi_line_content_keeps_its_line_breaks():
    """The dialect stores a raw newline, which the rich parser would otherwise collapse to a space."""
    assert stored("line one\nline two\n\nline four") == "line one<br/>line two<br/><br/>line four"


def test_stored_content_maps_its_formatting_tags():
    assert stored("<b>bold</b> and <i>italic</i>") == "<b>bold</b> and <i>italic</i>"


def test_stored_content_escapes_the_text_around_its_tags():
    assert stored("<b>a < b</b> & c") == "<b>a &lt; b</b> &amp; c"


def test_stored_content_drops_a_tag_the_dialect_cannot_read_and_keeps_its_text():
    assert stored("<b>kept</b> <marquee>still shown</marquee>") == "<b>kept</b> still shown"


def test_stored_content_closes_a_tag_the_row_left_open():
    assert stored("<b>never closed") == "<b>never closed</b>"


def test_stored_content_drops_a_close_that_opens_nothing():
    assert stored("a</i>b") == "ab"


def test_stored_content_never_raises_on_crossing_tags():
    """The close that cannot nest is dropped and the tags shut in the order they opened, so the
    markup stays well formed and no text is lost. The stretch after it keeps the inner tag."""
    assert stored("<b>a<i>b</b>c</i>") == "<b>a<i>bc</i></b>"


def test_stored_content_reports_what_it_could_not_read():
    with capture_logs() as logs:
        render_rich_tags("<marquee>x</marquee>", field="description")

    assert [entry for entry in logs if entry.get("event") == STORED_RENDER_EVENT] == [
        {
            "event": STORED_RENDER_EVENT,
            "log_level": "warning",
            "field": "description",
            "tag": "marquee",
            "reason": "unreadable",
            "dropped": 1,
        }
    ]


@pytest.mark.parametrize(
    "text, reason, tag",
    [("<marquee>x</marquee>", "unreadable", "marquee"), ("<b>x", "unclosed", "b"), ("x</i>", "unbalanced", "i")],
    ids=["unreadable", "unclosed", "unbalanced"],
)
def test_stored_content_names_why_each_tag_was_dropped(text: str, reason: str, tag: str):
    with capture_logs() as logs:
        render_rich_tags(text, field="title")

    reported = [entry for entry in logs if entry.get("event") == STORED_RENDER_EVENT]
    assert [(entry["reason"], entry["tag"]) for entry in reported] == [(reason, tag)]


def test_stored_content_counts_repeats_rather_than_logging_each_one():
    """A pathological row must not turn one render into hundreds of lines."""
    with capture_logs() as logs:
        render_rich_tags("<marquee>a</marquee><marquee>b</marquee><marquee>c</marquee>", field="title")

    reported = [entry for entry in logs if entry.get("event") == STORED_RENDER_EVENT]
    assert len(reported) == 1
    assert reported[0]["dropped"] == 3


def test_well_formed_stored_content_reports_nothing():
    with capture_logs() as logs:
        render_rich_tags("<b>fine</b> ${literal}", field="title")

    assert [entry for entry in logs if entry.get("event") == STORED_RENDER_EVENT] == []


# ---------------------------------------------------------------------------
# MessageBase.rich() and MessageBase.text()
# ---------------------------------------------------------------------------


class TaggedEngine(TranslationEngine):
    @override
    @classmethod
    def translate(cls, message_id: str, lang: str) -> str:
        return "Hello, <b>${name}</b>. <i>Tap ${button_go}</i>"


class SampleMessage(MessageBase):
    TEST = ""

    @override
    def translations_class(self) -> type[TranslationEngineProtocol]:
        return TaggedEngine


def test_rich_renders_the_translated_string_as_content():
    content = SampleMessage.TEST.rich(lang="en", name="a < b", button_go=button())

    assert content.html == f"Hello, <b>a &lt; b</b>. <i>Tap {BUTTON_MARKUP}</i>"


def test_rich_raises_on_a_placeholder_it_was_given_no_value_for():
    with pytest.raises(MissingRichPlaceholder, match=r"\$\{name\}"):
        SampleMessage.TEST.rich(lang="en", button_go=button())


class PlainEngine(TranslationEngine):
    @override
    @classmethod
    def translate(cls, message_id: str, lang: str) -> str:
        return "Room for ${count}, ${name}"


class PlainMessage(MessageBase):
    TEST = ""

    @override
    def translations_class(self) -> type[TranslationEngineProtocol]:
        return PlainEngine


def test_text_substitutes_scalars_into_an_unformatted_message():
    assert PlainMessage.TEST.text(lang="en", count=4, name="a < b") == "Room for 4, a < b"


def test_text_raises_on_a_placeholder_it_was_given_no_value_for():
    """A kwarg nobody passed is a defect of ours, so it stops here rather than reaching a reader as
    the literal `${name}` that only they can see."""
    with pytest.raises(MissingRichPlaceholder, match=r"\$\{name\}"):
        PlainMessage.TEST.text(lang="en", count=4)


def test_text_raises_on_a_message_that_renders_with_formatting():
    """Flattening here would drop the emphasis with nothing to say so, so it is the call site that
    has to give the formatting up, by asking for `rich(...).text` instead."""
    with pytest.raises(FormattedMessageAsText, match="SampleMessage.TEST"):
        SampleMessage.TEST.text(lang="en", name="World", button_go=button())

    assert SampleMessage.TEST.rich(lang="en", name="World", button_go=button()).text == "Hello, World. Tap Go"
