"""The tag dialect is the storage format for every meeting title and description, and no second
copy of the user's original entities is kept anywhere. These tests pin the two lines that are the
only evidence a round-trip lost something, and the silence of the pure functions underneath them."""

import pytest
from structlog.testing import capture_logs
from structlog.typing import EventDict
from telegram import MessageEntity

from mitup_bot.utils.entities import capture_tagged_text, parse_format_tags, parse_stored_tagged_text

DROPPED_EVENT = "Dropped an unsupported message entity"
PARSE_FAILURE_EVENT = "Stored tagged text did not parse"


def warnings_for(logs: list[EventDict], event: str) -> list[EventDict]:
    return [entry for entry in logs if entry["event"] == event and entry["log_level"] == "warning"]


# ---------------------------------------------------------------------------
# capture_tagged_text() — what the dialect could not carry in
# ---------------------------------------------------------------------------


def test_capture_reports_an_entity_type_the_dialect_cannot_store():
    blockquote = MessageEntity(type=MessageEntity.BLOCKQUOTE, offset=0, length=5)

    with capture_logs() as logs:
        tagged = capture_tagged_text("hello world", [blockquote], field="description")

    assert tagged == "hello world"
    assert warnings_for(logs, DROPPED_EVENT) == [
        {
            "event": DROPPED_EVENT,
            "log_level": "warning",
            "field": "description",
            "entity_type": MessageEntity.BLOCKQUOTE,
            "reason": "unsupported_entity_type",
            "dropped": 1,
        }
    ]


def test_capture_reports_a_partially_overlapping_span():
    """Telegram is not supposed to send these, which is exactly why the day it does must be visible.

    The discard is a bare `continue` in the middle of the serializer, and the user's screen simply
    shows unstyled text afterwards, so without this line the entity vanishing is indistinguishable
    from the user never having applied it.
    """
    bold = MessageEntity(type=MessageEntity.BOLD, offset=0, length=4)
    italic = MessageEntity(type=MessageEntity.ITALIC, offset=2, length=4)

    with capture_logs() as logs:
        capture_tagged_text("abcdef", [bold, italic], field="title")

    dropped = warnings_for(logs, DROPPED_EVENT)
    assert [(entry["reason"], entry["entity_type"], entry["field"]) for entry in dropped] == [
        ("overlapping_span", MessageEntity.ITALIC, "title")
    ]


def test_capture_groups_repeated_drops_into_one_line():
    """A heavily formatted message must not turn one edit into dozens of lines."""
    quotes = [MessageEntity(type=MessageEntity.BLOCKQUOTE, offset=offset, length=1) for offset in range(3)]

    with capture_logs() as logs:
        capture_tagged_text("abc", quotes, field="title")

    assert [entry["dropped"] for entry in warnings_for(logs, DROPPED_EVENT)] == [3]


def test_capture_is_silent_when_the_dialect_carries_everything():
    bold = MessageEntity(type=MessageEntity.BOLD, offset=0, length=5)

    with capture_logs() as logs:
        assert capture_tagged_text("hello", [bold], field="title") == "<b>hello</b>"

    assert warnings_for(logs, DROPPED_EVENT) == []


# ---------------------------------------------------------------------------
# parse_stored_tagged_text() — what this build could not read back
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stored, reason, tag",
    [
        ("<marquee>hi</marquee>", "unknown_tag", "marquee"),
        ('<a href="">hi</a>', "malformed_attributes", "a"),
        ("<tg-emoji>hi</tg-emoji>", "malformed_attributes", "tg-emoji"),
        ("hi</b>", "unbalanced_close", "b"),
        ("<b>hi", "unclosed_tag", "b"),
    ],
)
def test_a_stored_value_names_the_tag_this_build_could_not_resolve(stored: str, reason: str, tag: str):
    with capture_logs() as logs:
        parse_stored_tagged_text(stored, field="title")

    failures = warnings_for(logs, PARSE_FAILURE_EVENT)
    assert [(entry["reason"], entry["tag"], entry["field"]) for entry in failures] == [(reason, tag, "title")]
    assert all(isinstance(entry["offset"], int) for entry in failures)


def test_a_clean_stored_value_says_nothing():
    with capture_logs() as logs:
        assert parse_stored_tagged_text("<b>hello</b>", field="description").text == "hello"

    assert warnings_for(logs, PARSE_FAILURE_EVENT) == []


def test_the_catalog_renderer_stays_silent_on_the_same_input():
    """Dropping a tag outside the subset is `parse_format_tags`' contract, not an anomaly.

    Every translated string in the bot renders through it, so reporting there would bury the stored
    population — the one with no second copy to compare against — in catalog noise.
    """
    with capture_logs() as logs:
        parse_format_tags("<marquee>hi</marquee><b>x", {})

    assert warnings_for(logs, PARSE_FAILURE_EVENT) == []
