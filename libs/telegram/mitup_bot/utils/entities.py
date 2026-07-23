"""Entity-based message rendering primitives for Telegram entity formatting."""

from __future__ import annotations

import datetime as dt
import html
import re
from collections.abc import Iterable, Sequence
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

    def __init__(self, text: str, entities: list[MessageEntity] | None = None):
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
        shifted = [shift_entity(e, offset) for e in self._entities]
        return FormattedText(prefix_text + self._text, list(prefix_entities) + shifted)

    def append(self, suffix: str | FormattedText) -> FormattedText:
        """Return a new `FormattedText` with *suffix* appended, merging entities."""
        suffix_text = suffix if isinstance(suffix, str) else suffix.text
        suffix_entities = [] if isinstance(suffix, str) else suffix.entities
        offset = utf16_len(self._text)
        shifted_suffix = [shift_entity(e, offset) for e in suffix_entities]
        return FormattedText(self._text + suffix_text, self._entities + shifted_suffix)

    def wrap(self, entity_type: str) -> FormattedText:
        """Return a new `FormattedText` with one *entity_type* entity spanning the whole text."""
        if not self._text:
            return self
        spanning = MessageEntity(type=entity_type, offset=0, length=utf16_len(self._text))
        return FormattedText(self._text, [spanning, *self._entities])

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


def shift_entity(entity: MessageEntity, offset: int) -> MessageEntity:
    """Return a copy of *entity* with its offset shifted by *offset* UTF-16 code units."""
    # MessageEntity is frozen; construct a new instance with the adjusted offset.
    return MessageEntity(
        type=entity.type,
        offset=entity.offset + offset,
        length=entity.length,
        url=entity.url,
        custom_emoji_id=entity.custom_emoji_id,
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


def render_bold(plain: str, value: Bold) -> list[MessageEntity]:
    offset, length = utf16_len(plain), utf16_len(value.text)
    return [MessageEntity(type=MessageEntity.BOLD, offset=offset, length=length)]


def render_italic(plain: str, value: Italic) -> list[MessageEntity]:
    offset, length = utf16_len(plain), utf16_len(value.text)
    return [MessageEntity(type=MessageEntity.ITALIC, offset=offset, length=length)]


def render_bold_italic(plain: str, value: BoldItalic) -> list[MessageEntity]:
    offset, length = utf16_len(plain), utf16_len(value.text)
    return [
        MessageEntity(type=MessageEntity.BOLD, offset=offset, length=length),
        MessageEntity(type=MessageEntity.ITALIC, offset=offset, length=length),
    ]


def render_link(plain: str, value: Link) -> list[MessageEntity]:
    offset, length = utf16_len(plain), utf16_len(value.text)
    return [MessageEntity(type=MessageEntity.TEXT_LINK, offset=offset, length=length, url=value.url)]


def render_entity_datetime(plain: str, value: EntityDateTime) -> list[MessageEntity]:
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
                entities.extend(render_bold(plain, value))
                plain += value.text
            case Italic():
                entities.extend(render_italic(plain, value))
                plain += value.text
            case BoldItalic():
                entities.extend(render_bold_italic(plain, value))
                plain += value.text
            case Link():
                entities.extend(render_link(plain, value))
                plain += value.text
            case EntityDateTime():
                entities.extend(render_entity_datetime(plain, value))
                plain += value.text
            case FormattedText():
                prefix_len = utf16_len(plain)
                entities.extend(shift_entity(e, prefix_len) for e in value.entities)
                plain += value.text
            case Template():
                nested = render(value)
                prefix_len = utf16_len(plain)
                entities.extend(shift_entity(e, prefix_len) for e in nested.entities)
                plain += nested.text
            case _:
                plain += str(value)

    return FormattedText(plain, entities)


TELEGRAM_DATETIME_LINK_URL = "https://telegram.org/blog/member-tags-disable-sharing-and-more#time-and-date-formatting"
# Kept out of the t-string below: a quoted literal (with its apostrophe) inside a same-quoted
# interpolation is valid Python but derails regex-based highlighters for the rest of the file.
TELEGRAM_DATETIME_LINK_TEXT = "Telegram's date & time formatting"


def build_datetime_link() -> FormattedText:
    """Build the `FormattedText` for Telegram's date & time formatting help link."""
    return render(t"{Link(TELEGRAM_DATETIME_LINK_TEXT, TELEGRAM_DATETIME_LINK_URL)}")


# --- parse_format_tags() ---

# A tag is `<name attr="value" ...>` or a closing `</name>`; attributes are
# optional `name="value"` (or single-quoted) pairs. The variable alternative
# stays byte-identical to the original so `${var}` substitution is unchanged.
# Tag names require a leading letter, so `<3`, a bare `<`, and `<https://…>`
# never match and are preserved verbatim.
TOKEN_RE = re.compile(
    r"<(?P<close>/?)(?P<tag>[a-z][a-z-]*)"
    r"""(?P<attrs>(?:\s+[a-zA-Z-]+\s*=\s*(?:"[^"]*"|'[^']*'))*)\s*>"""
    r"|\$\{(?P<var>\w+)\}"
)

# Tag name → Telegram entity type. `<a>` (link) and `<span>` (spoiler) depend on
# their attributes and are resolved in `resolve_open_tag` instead.
STYLE_MAP: dict[str, str] = {
    "b": MessageEntity.BOLD,
    "strong": MessageEntity.BOLD,
    "i": MessageEntity.ITALIC,
    "em": MessageEntity.ITALIC,
    "u": MessageEntity.UNDERLINE,
    "ins": MessageEntity.UNDERLINE,
    "s": MessageEntity.STRIKETHROUGH,
    "strike": MessageEntity.STRIKETHROUGH,
    "del": MessageEntity.STRIKETHROUGH,
    "code": MessageEntity.CODE,
    "pre": MessageEntity.PRE,
    "spoiler": MessageEntity.SPOILER,
    "tg-spoiler": MessageEntity.SPOILER,
    "blockquote": MessageEntity.BLOCKQUOTE,
}

ATTR_RE = re.compile(r"""([a-zA-Z-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")


@dataclass
class OpenTag:
    """An unclosed opening tag: the offset it began at and the entity to emit on close."""

    offset: int
    entity_type: str | None
    url: str | None = None
    custom_emoji_id: str | None = None


def parse_tag_attributes(attrs: str) -> dict[str, str]:
    """Parse an attribute blob (`href="x" class='y'`) into a name→value mapping."""
    return {m.group(1).lower(): m.group(2) if m.group(2) is not None else m.group(3) for m in ATTR_RE.finditer(attrs)}


def resolve_open_tag(tag: str, attrs: str, offset: int) -> OpenTag:
    """Resolve an opening tag and its attributes into the `OpenTag` to track.

    Tags that carry no entity — an unknown tag, an `<a>` without `href`, a
    `<span>` that is not a `tg-spoiler`, or a `<tg-emoji>` without `emoji-id` —
    resolve with `entity_type=None`, so the tag is stripped from the output
    without producing an entity.
    """
    match tag:
        case "a":
            href = parse_tag_attributes(attrs).get("href")
            return OpenTag(offset, MessageEntity.TEXT_LINK if href else None, url=href)
        case "span":
            classes = parse_tag_attributes(attrs).get("class", "").split()
            return OpenTag(offset, MessageEntity.SPOILER if "tg-spoiler" in classes else None)
        case "tg-emoji":
            emoji_id = parse_tag_attributes(attrs).get("emoji-id")
            return OpenTag(offset, MessageEntity.CUSTOM_EMOJI if emoji_id else None, custom_emoji_id=emoji_id)
        case _:
            return OpenTag(offset, STYLE_MAP.get(tag))


def parse_format_tags(text: str, substitutions: dict[str, str | FormattedText]) -> FormattedText:
    """Parse a tag-annotated translated string into a `FormattedText`.

    Supports the Telegram HTML subset: `<b>`/`<strong>`, `<i>`/`<em>`,
    `<u>`/`<ins>`, `<s>`/`<strike>`/`<del>`, `<code>`, `<pre>`, `<blockquote>`,
    `<spoiler>`/`<tg-spoiler>`/`<span class="tg-spoiler">`,
    `<tg-emoji emoji-id="…">` custom emoji, and `<a href="…">` links.
    Variable placeholders use `${varname}` syntax.

    Substitution values may be plain `str` or `FormattedText`.  When a
    `FormattedText` value is substituted its entities are preserved with
    offsets adjusted to their final position.  Plain-string values are never
    scanned for tags, so user-supplied content cannot introduce spurious entities.

    Tags may be arbitrarily nested; each tag tracks its own start offset
    independently.  Unclosed, unbalanced, or unknown tags are silently dropped.
    To add a new alias, insert an entry into `STYLE_MAP`.

    HTML character references (`&amp;`, `&lt;`, `&#39;`, …) in the literal text
    runs are decoded to their characters, matching Telegram's HTML spec. Decoding
    happens only on the non-tag segments and only after tag detection, so an
    escaped `&lt;` renders as a literal `<` and is never re-parsed as a tag.
    """
    plain = ""
    utf16_offset = 0
    entities: list[MessageEntity] = []
    active: dict[str, OpenTag] = {}  # tag name → opening tag awaiting its close
    cursor = 0

    def flush(s: str):
        nonlocal plain, utf16_offset
        plain += s
        utf16_offset += utf16_len(s)

    def flush_literal(s: str):
        flush(html.unescape(s))

    def substitute(var: str):
        value = substitutions.get(var, f"${{{var}}}")
        if isinstance(value, FormattedText):
            entities.extend(shift_entity(e, utf16_offset) for e in value.entities)
            flush(value.text)
        else:
            flush(value)

    def close_tag(tag: str):
        open_tag = active.pop(tag, None)
        if open_tag is None or open_tag.entity_type is None:
            return
        length = utf16_offset - open_tag.offset
        if length > 0:
            entities.append(
                MessageEntity(
                    type=open_tag.entity_type,
                    offset=open_tag.offset,
                    length=length,
                    url=open_tag.url,
                    custom_emoji_id=open_tag.custom_emoji_id,
                )
            )

    for m in TOKEN_RE.finditer(text):
        flush_literal(text[cursor : m.start()])
        cursor = m.end()
        if (var := m.group("var")) is not None:
            substitute(var)
        elif m.group("close"):
            close_tag(m.group("tag"))
        else:
            active[m.group("tag")] = resolve_open_tag(m.group("tag"), m.group("attrs"), utf16_offset)

    flush_literal(text[cursor:])
    entities.sort(key=lambda e: (e.offset, e.type))
    return FormattedText(plain, entities)


# --- serialize_entities() ---

# Entity type → tag emitted by `serialize_entities`. Only these types survive
# serialization; every other entity is dropped and its text stays unstyled.
ENTITY_TAG_MAP: dict[str, str] = {
    MessageEntity.BOLD: "b",
    MessageEntity.ITALIC: "i",
    MessageEntity.UNDERLINE: "u",
    MessageEntity.STRIKETHROUGH: "s",
    MessageEntity.SPOILER: "tg-spoiler",
    MessageEntity.CUSTOM_EMOJI: "tg-emoji",
}


def is_serializable_entity(entity: MessageEntity) -> bool:
    if entity.type == MessageEntity.CUSTOM_EMOJI:
        return entity.custom_emoji_id is not None
    return entity.type in ENTITY_TAG_MAP


def open_tag_markup(entity: MessageEntity) -> str:
    if entity.type == MessageEntity.CUSTOM_EMOJI:
        assert entity.custom_emoji_id is not None, "filtered by is_serializable_entity"
        return f'<tg-emoji emoji-id="{html.escape(entity.custom_emoji_id)}">'
    return f"<{ENTITY_TAG_MAP[entity.type]}>"


def serialize_entities(text: str, entities: Sequence[MessageEntity]) -> str:
    """Render *(text, entities)* as a tag-annotated string for `parse_format_tags`.

    The capture-side inverse of `parse_format_tags`: literal text is
    HTML-escaped so user-typed markup (`<b>`, `&amp;`, `<3`, …) round-trips as
    literal characters, and entity spans are wrapped in the tags listed in
    `ENTITY_TAG_MAP`; entities of any other type are dropped with their text
    kept unstyled. Entities nest by sorting on `(offset, -length)`; an entity
    that partially overlaps an already-open span without nesting inside it is
    dropped — the earlier-starting entity wins. Telegram never sends partially
    overlapping entities, so that rule is purely defensive.
    """
    encoded = text.encode("utf-16-le")
    parts: list[str] = []
    open_spans: list[tuple[int, str]] = []  # (end offset, tag name), innermost last
    cursor = 0

    def flush_until(end: int):
        nonlocal cursor
        parts.append(html.escape(encoded[cursor * 2 : end * 2].decode("utf-16-le")))
        cursor = end

    def close_spans_ending_by(offset: int):
        while open_spans and open_spans[-1][0] <= offset:
            end, tag = open_spans.pop()
            flush_until(end)
            parts.append(f"</{tag}>")

    for entity in sorted(filter(is_serializable_entity, entities), key=lambda e: (e.offset, -e.length)):
        close_spans_ending_by(entity.offset)
        end = entity.offset + entity.length
        if open_spans and end > open_spans[-1][0]:
            continue
        flush_until(entity.offset)
        parts.append(open_tag_markup(entity))
        open_spans.append((end, ENTITY_TAG_MAP[entity.type]))

    close_spans_ending_by(utf16_len(text))
    flush_until(utf16_len(text))
    return "".join(parts)


def strip_tags(tagged: str) -> str:
    """Return the visible plain text of a tag-annotated string."""
    return parse_format_tags(tagged, {}).text
