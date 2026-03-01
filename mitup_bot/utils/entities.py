"""Entity-based message rendering primitives for Telegram entity formatting."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from string.templatelib import Interpolation, Template

from telegram import MessageEntity

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
    """Telegram ``date_time`` entity — each viewer sees the time in their own locale."""

    text: str
    unix_time: int
    date_time_format: str | None = None


# --- DateTimeMessageEntity ---


class DateTimeMessageEntity(MessageEntity):
    """PTB ``MessageEntity`` subclass carrying the ``unix_time`` field for ``date_time`` entities."""

    unix_time: int
    date_time_format: str | None

    __slots__ = ("date_time_format", "unix_time")

    def __init__(
        self,
        offset: int,
        length: int,
        unix_time: int,
        date_time_format: str | None = None,
    ) -> None:
        super().__init__(type="date_time", offset=offset, length=length)
        # MessageEntity calls _freeze() in __init__, so we bypass the guard here.
        object.__setattr__(self, "unix_time", unix_time)
        object.__setattr__(self, "date_time_format", date_time_format)

    def to_dict(self, recursive: bool = True) -> dict:
        d = super().to_dict(recursive=recursive)
        d["unix_time"] = self.unix_time
        if self.date_time_format is not None:
            d["date_time_format"] = self.date_time_format
        return d


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
        DateTimeMessageEntity(
            offset=offset,
            length=length,
            unix_time=value.unix_time,
            date_time_format=value.date_time_format,
        )
    ]


def render(template: Template) -> tuple[str, list[MessageEntity]]:
    """Convert a t-string into a ``(plain_text, entities)`` pair with UTF-16 offsets."""
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
            case _:
                plain += str(value)

    return plain, entities


# --- parse_md_markers() ---

_MD_ESCAPE_RE = re.compile(r"\\(.)")
_VAR_RE = re.compile(r"\$\{(\w+)\}")
_BOLD_RE = re.compile(r"\*([^*]+)\*")
_ITALIC_RE = re.compile(r"_([^_]+)_")
# Non-greedy to allow ${var} placeholders with underscores inside _*…*_ spans.
_BOLD_ITALIC_RE = re.compile(r"_\*(.+?)\*_")


class EntityType(StrEnum):
    BOLD = "bold"
    ITALIC = "italic"


@dataclass
class _MarkerSpan:
    outer_start: int
    outer_end: int
    inner_start: int
    inner_end: int
    types: list[EntityType]


@dataclass
class _VarSpan:
    start: int
    end: int
    name: str
    replacement: str


@dataclass
class _EscapeSpan:
    start: int
    end: int
    char: str


def _collect_marker_spans(text: str, escaped_positions: set[int]) -> list[_MarkerSpan]:
    """
    Scan *text* for *…*, _…_, and _*…*_ marker spans.

    *escaped_positions* is the set of character indices whose preceding backslash
    has already been identified by ``_MD_ESCAPE_RE``. Any marker match whose opening
    or closing delimiter lands on one of these positions is skipped — the character
    is a literal, not a formatting marker.

    We only use bold, italic and bold-italic spans. If something else needs to be supported,
    it needs to be implemented as well.
    """
    spans: list[_MarkerSpan] = []
    visited: set[int] = set()

    # _*…*_ must be found first to avoid double-counting the inner * and _ markers.
    for m in _BOLD_ITALIC_RE.finditer(text):
        if m.start() in escaped_positions or m.end() - 1 in escaped_positions:
            continue
        spans.append(
            _MarkerSpan(
                outer_start=m.start(),
                outer_end=m.end(),
                inner_start=m.start(1),
                inner_end=m.end(1),
                types=[EntityType.BOLD, EntityType.ITALIC],
            )
        )
        visited.add(m.start())

    for m in _BOLD_RE.finditer(text):
        if m.start() in escaped_positions or m.end() - 1 in escaped_positions:
            continue
        if m.start() not in visited and m.start() - 1 not in visited:
            spans.append(
                _MarkerSpan(
                    outer_start=m.start(),
                    outer_end=m.end(),
                    inner_start=m.start(1),
                    inner_end=m.end(1),
                    types=[EntityType.BOLD],
                )
            )
            visited.add(m.start())

    for m in _ITALIC_RE.finditer(text):
        if m.start() in escaped_positions or m.end() - 1 in escaped_positions:
            continue
        if m.start() not in visited:
            spans.append(
                _MarkerSpan(
                    outer_start=m.start(),
                    outer_end=m.end(),
                    inner_start=m.start(1),
                    inner_end=m.end(1),
                    types=[EntityType.ITALIC],
                )
            )
            visited.add(m.start())

    spans.sort(key=lambda s: s.outer_start)
    return spans


def _collect_var_spans(text: str, substitutions: dict[str, str]) -> list[_VarSpan]:
    """Find every ``${varname}`` placeholder and resolve it against *substitutions*.

    Unknown variables are left as-is (``${name}``), so the output length is always
    predictable for offset calculations even when a key is missing.
    """
    return [
        _VarSpan(
            start=m.start(),
            end=m.end(),
            name=m.group(1),
            replacement=substitutions.get(m.group(1), f"${{{m.group(1)}}}"),
        )
        for m in _VAR_RE.finditer(text)
    ]


def _build_skip_positions(
    raw_spans: list[_MarkerSpan],
    escape_spans: list[_EscapeSpan],
) -> set[int]:
    """Collect template character indices that must be silently dropped from the output.

    Marker delimiters (``*``, ``_``) and backslashes from MarkdownV2 escape sequences
    are structural — they convey formatting intent but must not appear in the plain text
    sent to Telegram alongside the entity list.
    """
    skip: set[int] = set()
    for span in raw_spans:
        # _*…*_ — drop both the outer _ and inner * on each side.
        skip.add(span.outer_start)
        if len(span.types) == 2:
            skip.add(span.outer_start + 1)
            skip.add(span.outer_end - 2)
        skip.add(span.outer_end - 1)
    for es in escape_spans:
        skip.add(es.start)
    return skip


def _build_template_to_utf16(
    text: str,
    skip_positions: set[int],
    var_spans: list[_VarSpan],
) -> tuple[str, dict[int, int]]:
    """Single-pass walk: produce the plain output and map each template index to a UTF-16 offset."""
    var_start_map: dict[int, _VarSpan] = {vs.start: vs for vs in var_spans}
    mapping: dict[int, int] = {}
    output = ""
    i = 0
    n = len(text)

    while i < n:
        mapping[i] = utf16_len(output)

        if i in var_start_map:
            vs = var_start_map[i]
            output += vs.replacement
            i = vs.end
            continue

        if i in skip_positions:
            i += 1
            continue

        output += text[i]
        i += 1

    mapping[n] = utf16_len(output)
    return output, mapping


def _spans_to_entities(
    raw_spans: list[_MarkerSpan],
    template_to_utf16: dict[int, int],
) -> list[MessageEntity]:
    """Convert collected marker spans to ``MessageEntity`` objects using the UTF-16 offset map.

    Each span's inner bounds are looked up in *template_to_utf16* to get the correct
    offset and length in the final plain text. Bold-italic spans produce two entities
    at the same position.
    """
    entities: list[MessageEntity] = []
    for span in raw_spans:
        offset_utf16 = _nearest_utf16(template_to_utf16, span.inner_start)
        end_utf16 = _nearest_utf16(template_to_utf16, span.inner_end)
        length_utf16 = end_utf16 - offset_utf16
        if length_utf16 <= 0:
            continue
        entities.extend(
            MessageEntity(type=entity_type, offset=offset_utf16, length=length_utf16) for entity_type in span.types
        )
    entities.sort(key=lambda e: (e.offset, e.type))
    return entities


def parse_md_markers(
    text: str,
    substitutions: dict[str, str],
) -> tuple[str, list[MessageEntity]]:
    """Parse a MarkdownV2-annotated translated string into a ``(plain_text, entities)`` pair.

    *text* is a plain ``str`` from the gettext catalogue — not a t-string. Markers are
    scanned before variable substitution so that user-supplied values never produce spurious
    entities, with offsets adjusted for each substitution's length afterward.
    """
    escape_spans = [_EscapeSpan(start=m.start(), end=m.end(), char=m.group(1)) for m in _MD_ESCAPE_RE.finditer(text)]
    escaped_positions = {es.start + 1 for es in escape_spans}
    raw_spans = _collect_marker_spans(text, escaped_positions)
    var_spans = _collect_var_spans(text, substitutions)
    skip_positions = _build_skip_positions(raw_spans, escape_spans)
    output, template_to_utf16 = _build_template_to_utf16(text, skip_positions, var_spans)
    entities = _spans_to_entities(raw_spans, template_to_utf16)
    return output, entities


def _nearest_utf16(mapping: dict[int, int], pos: int) -> int:
    """Fall back to the nearest recorded position when *pos* was consumed mid-sequence."""
    if pos in mapping:
        return mapping[pos]
    return next((mapping[p] for p in range(pos - 1, -1, -1) if p in mapping), 0)
