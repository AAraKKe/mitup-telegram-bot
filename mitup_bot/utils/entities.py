"""Entity-based message rendering primitives for Telegram entity formatting."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from dataclasses import dataclass
from string.templatelib import Interpolation, Template

from telegram import MessageEntity


class FormattedText:
    """Immutable plain-text + entity pair for Telegram messages.

    Wraps the `(text, entities)` pair that Telegram expects and provides
    mutation-free helpers for composing messages without manually tracking
    UTF-16 entity offsets.
    """

    __slots__ = ("_entities", "_text")

    def __init__(self, text: str, entities: list[MessageEntity] | None = None) -> None:
        self._text = text
        self._entities: list[MessageEntity] = list(entities) if entities else []

    @property
    def text(self) -> str:
        return self._text

    @property
    def entities(self) -> list[MessageEntity]:
        """Return the entity list (empty when there are none)."""
        return self._entities

    def prepend(self, prefix: str | FormattedText) -> FormattedText:
        """Return a new `FormattedText` with *prefix* prepended, shifting entity offsets."""
        prefix_text = prefix if isinstance(prefix, str) else prefix.text
        prefix_entities = [] if isinstance(prefix, str) else prefix.entities
        offset = utf16_len(prefix_text)
        shifted = [_shift_entity(e, offset) for e in self._entities]
        return FormattedText(prefix_text + self._text, list(prefix_entities) + shifted)

    def append(self, suffix: str | FormattedText) -> FormattedText:
        """Return a new `FormattedText` with *suffix* appended, merging entities."""
        suffix_text = suffix if isinstance(suffix, str) else suffix.text
        suffix_entities = [] if isinstance(suffix, str) else suffix.entities
        offset = utf16_len(self._text)
        shifted_suffix = [_shift_entity(e, offset) for e in suffix_entities]
        return FormattedText(self._text + suffix_text, self._entities + shifted_suffix)

    @classmethod
    def join(cls, separator: str, parts: Iterable[str | FormattedText]) -> FormattedText:
        """Join *parts* with *separator*, preserving entities from each part.

        Analogous to `str.join` but entity offsets are recalculated as parts
        are concatenated so the final object is always consistent.
        """
        result: FormattedText | None = None
        for part in parts:
            ft = part if isinstance(part, FormattedText) else FormattedText(part)
            result = ft if result is None else result.append(separator).append(ft)
        return result if result is not None else FormattedText("")

    def __str__(self) -> str:
        return self._text

    def __repr__(self) -> str:
        return f"FormattedText({self._text!r}, entities={self._entities!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FormattedText):
            return NotImplemented
        return self._text == other._text and self._entities == other._entities


# --- Typed wrapper dataclasses ---


@dataclass
class Bold:
    text: str


@dataclass
class Italic:
    text: str


@dataclass
class BoldItalic:
    text: str


@dataclass
class Link:
    text: str
    url: str


@dataclass
class EntityDateTime:
    """Telegram `date_time` entity — each viewer sees the time in their own locale."""

    text: str
    unix_time: dt.datetime
    date_time_format: str | None = None


def _shift_entity(entity: MessageEntity, offset: int) -> MessageEntity:
    """Return a copy of *entity* with its offset shifted by *offset* UTF-16 code units."""
    # MessageEntity is frozen; construct a new instance with the adjusted offset.
    return MessageEntity(
        type=entity.type,
        offset=entity.offset + offset,
        length=entity.length,
        url=entity.url,
        unix_time=entity.unix_time,
        date_time_format=entity.date_time_format,
    )


def strip_entity_from_text(text: str, entity: MessageEntity) -> str:
    """Return *text* with the UTF-16 span covered by *entity* removed.

    Whitespace adjacent to the removed span is trimmed so that a single space
    between the left and right parts is preserved rather than a double space.
    """
    encoded = text.encode("utf-16-le")
    start = entity.offset * 2
    end = (entity.offset + entity.length) * 2
    left = encoded[:start].decode("utf-16-le").rstrip()
    right = encoded[end:].decode("utf-16-le").lstrip()
    return f"{left} {right}" if left and right else left + right


# --- UTF-16 helper ---


def utf16_len(s: str) -> int:
    """Return the length of *s* measured in UTF-16 code units.

    Telegram entity offsets are expressed in UTF-16 code units, not Unicode
    code points. Characters outside the BMP (e.g. emoji) occupy two units.
    """
    return len(s.encode("utf-16-le")) // 2


# --- render() ---


def _render_bold(plain: str, value: Bold) -> list[MessageEntity]:
    offset, length = utf16_len(plain), utf16_len(value.text)
    return [MessageEntity(type=MessageEntity.BOLD, offset=offset, length=length)]


def _render_italic(plain: str, value: Italic) -> list[MessageEntity]:
    offset, length = utf16_len(plain), utf16_len(value.text)
    return [MessageEntity(type=MessageEntity.ITALIC, offset=offset, length=length)]


def _render_bold_italic(plain: str, value: BoldItalic) -> list[MessageEntity]:
    offset, length = utf16_len(plain), utf16_len(value.text)
    return [
        MessageEntity(type=MessageEntity.BOLD, offset=offset, length=length),
        MessageEntity(type=MessageEntity.ITALIC, offset=offset, length=length),
    ]


def _render_link(plain: str, value: Link) -> list[MessageEntity]:
    offset, length = utf16_len(plain), utf16_len(value.text)
    return [MessageEntity(type=MessageEntity.TEXT_LINK, offset=offset, length=length, url=value.url)]


def _render_entity_datetime(plain: str, value: EntityDateTime) -> list[MessageEntity]:
    offset, length = utf16_len(plain), utf16_len(value.text)
    return [
        MessageEntity(
            type=MessageEntity.DATE_TIME,
            offset=offset,
            length=length,
            unix_time=value.unix_time,
            date_time_format=value.date_time_format,
        )
    ]


def render(template: Template) -> FormattedText:
    """Convert a t-string into a `FormattedText` with UTF-16 entity offsets."""
    plain = ""
    entities: list[MessageEntity] = []

    for part in template:
        if isinstance(part, str):
            plain += part
            continue

        assert isinstance(part, Interpolation)
        value = part.value

        match value:
            case str():
                plain += value
            case Bold():
                entities.extend(_render_bold(plain, value))
                plain += value.text
            case Italic():
                entities.extend(_render_italic(plain, value))
                plain += value.text
            case BoldItalic():
                entities.extend(_render_bold_italic(plain, value))
                plain += value.text
            case Link():
                entities.extend(_render_link(plain, value))
                plain += value.text
            case EntityDateTime():
                entities.extend(_render_entity_datetime(plain, value))
                plain += value.text
            case FormattedText():
                prefix_len = utf16_len(plain)
                entities.extend(_shift_entity(e, prefix_len) for e in value.entities)
                plain += value.text
            case Template():
                nested = render(value)
                prefix_len = utf16_len(plain)
                entities.extend(_shift_entity(e, prefix_len) for e in nested.entities)
                plain += nested.text
            case _:
                plain += str(value)

    return FormattedText(plain, entities)


TELEGRAM_DATETIME_LINK_URL = "https://telegram.org/blog/member-tags-disable-sharing-and-more#time-and-date-formatting"


def build_datetime_link() -> FormattedText:
    """Build the `FormattedText` for Telegram's date & time formatting help link."""
    return render(t"{Link("Telegram's date & time formatting", TELEGRAM_DATETIME_LINK_URL)}")


# --- parse_format_tags() ---

_TOKEN_RE = re.compile(r"<(/?[a-z]+)>|\$\{(\w+)\}")

STYLE_MAP: dict[str, str] = {
    "b": "bold",
    "i": "italic",
    "u": "underline",
    "s": "strikethrough",
    "code": "code",
    "pre": "pre",
    "spoiler": "spoiler",
}


def parse_format_tags(text: str, substitutions: dict[str, str | FormattedText]) -> FormattedText:
    """Parse a tag-annotated translated string into a `FormattedText`.

    Supported formatting tags: `<b>`, `<i>`, `<u>`, `<s>`, `<code>`,
    `<pre>`, `<spoiler>`. Variable placeholders use `${varname}` syntax.

    Substitution values may be plain `str` or `FormattedText`.  When a
    `FormattedText` value is substituted its entities are preserved with
    offsets adjusted to their final position.  Plain-string values are never
    scanned for tags, so user-supplied content cannot introduce spurious entities.

    Tags may be arbitrarily nested; each style tracks its own start offset
    independently.  Unclosed tags are silently dropped.  To add a new entity
    type, insert an entry into `STYLE_MAP`.
    """
    plain = ""
    utf16_offset = 0
    entities: list[MessageEntity] = []
    active: dict[str, int] = {}  # style → UTF-16 offset where the opening tag appeared
    cursor = 0

    def flush(s: str) -> None:
        nonlocal plain, utf16_offset
        plain += s
        utf16_offset += utf16_len(s)

    for m in _TOKEN_RE.finditer(text):
        flush(text[cursor : m.start()])
        cursor = m.end()

        tag, var = m.group(1), m.group(2)

        if var is not None:
            value = substitutions.get(var, m.group(0))
            if isinstance(value, FormattedText):
                for e in value.entities:
                    entities.append(_shift_entity(e, utf16_offset))
                flush(value.text)
            else:
                flush(value)
        elif tag.startswith("/"):
            style = tag[1:]
            if style in active:
                start = active.pop(style)
                length = utf16_offset - start
                if length > 0 and (etype := STYLE_MAP.get(style)):
                    entities.append(MessageEntity(type=etype, offset=start, length=length))
        else:
            active[tag] = utf16_offset

    flush(text[cursor:])
    entities.sort(key=lambda e: (e.offset, e.type))
    return FormattedText(plain, entities)
