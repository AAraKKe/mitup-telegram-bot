"""Rendering of message templates into `RichContent`.

Three authoring surfaces produce the same content: a t-string written in code, a catalog string
carrying `${var}` placeholders and the translation-source formatting tags, and a tagged string a
user typed that came back out of the database. All of them hand their values to `rich_value`, so a
button, a moment or a nested fragment reaches the wire the same way whichever surface named it, and
none of them can put a character on the wire without it passing through `RichContent`'s escaping.

The first two are strict, because their defects are ours: a placeholder with no value, a formatting
tag with no rich equivalent, and an interpolated value of a type the dialect cannot carry all raise
rather than reaching a reader as a defect they can see and nobody else can (a literal `${cap}`, a
stretch of text whose emphasis silently vanished, or a Python repr mid-sentence). Both are authored
by us and checked in CI, so a raise is a bug report before release.

Stored user content is the opposite case and `render_rich_tags` handles it apart. Its defects are
already in the database, unfixable by us and unreachable by any check, so a tag the dialect cannot
read is dropped and logged exactly as the entity reader does it, rather than raising and taking the
whole card down with it. Its `${...}` is text somebody typed, so substitution is off entirely.
"""

from __future__ import annotations

import html
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from string.templatelib import Interpolation, Template
from typing import cast

import structlog

from mitup_bot.emojis import Emojis
from mitup_bot.format_tags import STYLE_TAG_NAMES, TOKEN_RE
from mitup_bot.keyboards import ButtonConfig, ButtonRow, Keyboard
from mitup_bot.utils.entities import Bold, BoldItalic, EntityDateTime, Italic, Link, parse_tag_attributes
from mitup_bot.utils.rich_message import (
    RichContent,
    RichTag,
    anchor_markup,
    button_content,
    button_row_content,
    date_time_content,
    escape_attribute,
    keyboard_content,
)

log = structlog.get_logger(__name__)

RichParams = (
    str
    | int
    | float
    | Emojis
    | RichContent
    | Bold
    | Italic
    | BoldItalic
    | Link
    | EntityDateTime
    | ButtonConfig
    | ButtonRow
    | Keyboard
    | Template
)

# Placeholders whose value is a button rather than text are named apart, so a translator reading
# the catalog can tell that a button is coming without seeing the code that fills it.
BUTTON_PLACEHOLDER_PREFIX = "button_"


class RichTemplateError(ValueError):
    """A template could not be rendered into rich content."""


class UnsupportedRichInterpolation(RichTemplateError):
    def __init__(self, value: object):
        super().__init__(f"Value of type {type(value).__name__!r} cannot be interpolated into rich content")


class MissingRichPlaceholder(RichTemplateError):
    def __init__(self, name: str):
        super().__init__(f"Placeholder ${{{name}}} was given no value, so it would reach the reader verbatim")


class NonButtonInButtonPlaceholder(RichTemplateError):
    def __init__(self, name: str, value: object):
        super().__init__(f"Placeholder ${{{name}}} is named for a button but was given a {type(value).__name__!r}")


class ButtonOutsideButtonPlaceholder(RichTemplateError):
    def __init__(self, name: str):
        super().__init__(
            f"Placeholder ${{{name}}} was given a button, which only a "
            f"${{{BUTTON_PLACEHOLDER_PREFIX}...}} placeholder may carry"
        )


class UnsupportedRichFormatTag(RichTemplateError):
    def __init__(self, tag: str):
        super().__init__(f"Formatting tag {tag!r} has no rich-HTML equivalent")


class MalformedRichFormatTag(RichTemplateError):
    def __init__(self, tag: str, attribute: str):
        super().__init__(f"Formatting tag {tag!r} carries no {attribute!r}, which its rich-HTML tag needs")


class UnbalancedRichFormatTag(RichTemplateError):
    def __init__(self, tag: str):
        super().__init__(f"Closing tag </{tag}> does not close the tag that is open")


class UnclosedRichFormatTag(RichTemplateError):
    def __init__(self, tag: str):
        super().__init__(f"Tag <{tag}> is never closed, so its rich-HTML markup would not be well formed")


# --- Values ---


def is_button_row(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(entry, ButtonConfig) for entry in value)


def is_keyboard(value: object) -> bool:
    return isinstance(value, list) and all(is_button_row(entry) for entry in value)


def is_button_value(value: object) -> bool:
    """Whether *value* renders as buttons rather than as text."""
    return isinstance(value, ButtonConfig) or is_button_row(value) or is_keyboard(value)


def button_group_content(group: Sequence[object]) -> RichContent:
    """A keyboard row or a whole keyboard, told apart by what its entries are.

    Both are plain lists, so the entries are what distinguishes them. An empty list is neither and
    carries no buttons, which is the one reading that is right either way.
    """
    if not group:
        return RichContent()
    if is_button_row(group):
        return button_row_content(cast(ButtonRow, group))
    if is_keyboard(group):
        return keyboard_content(cast(Keyboard, group))
    raise UnsupportedRichInterpolation(group)


def rich_value(value: object, *, inside_pre: bool = False) -> RichContent:
    """Read an interpolated or substituted value as rich content.

    A `bool` is rejected ahead of `int`, which it subclasses: the only thing interpolating one
    could produce is the word "True" in the middle of a sentence.
    """
    match value:
        case RichContent():
            return value
        case bool():
            raise UnsupportedRichInterpolation(value)
        case str():
            return RichContent(value, inside_pre=inside_pre)
        case int() | float():
            return RichContent(str(value), inside_pre=inside_pre)
        case Emojis():
            # The one enum with a place in content: a closed vocabulary of glyphs whose str form
            # is the glyph itself. Any other enum still fails as an accident.
            return RichContent(str(value), inside_pre=inside_pre)
        case Bold():
            return RichContent(value.text, inside_pre=inside_pre).wrap(RichTag.BOLD)
        case Italic():
            return RichContent(value.text, inside_pre=inside_pre).wrap(RichTag.ITALIC)
        case BoldItalic():
            return RichContent(value.text, inside_pre=inside_pre).wrap(RichTag.ITALIC).wrap(RichTag.BOLD)
        case Link():
            return RichContent.link(value.text, value.url)
        case ButtonConfig():
            return button_content(value)
        case EntityDateTime():
            return date_time_content(value)
        case Template():
            return render_rich(value)
        case list():
            return button_group_content(value)
        case _:
            raise UnsupportedRichInterpolation(value)


# --- t-strings ---


def render_rich(template: Template) -> RichContent:
    """Render a t-string into rich content, escaping its literal text.

    Literal runs are content, not markup: a `<` written in the template reaches the reader as a
    `<`, and a newline becomes the `<br/>` the rich parser needs to keep it. Formatting comes from
    the interpolated values, each of which carries its own markup.
    """
    pieces = [
        RichContent(part).html if isinstance(part, str) else rich_value(cast(Interpolation, part).value).html
        for part in template
    ]
    return RichContent.from_markup("".join(pieces))


# --- Catalog strings ---

# Translation-source tag → the rich-HTML tag it becomes. The two dialects name almost every style
# alike; the aliases and `<spoiler>` are where they part. Tags carrying an attribute are resolved
# in `format_tag_markup` instead.
FORMAT_TAG_RICH_TAGS: dict[str, RichTag] = {
    "b": RichTag.BOLD,
    "strong": RichTag.BOLD,
    "i": RichTag.ITALIC,
    "em": RichTag.ITALIC,
    "u": RichTag.UNDERLINE,
    "ins": RichTag.UNDERLINE,
    "s": RichTag.STRIKETHROUGH,
    "strike": RichTag.STRIKETHROUGH,
    "del": RichTag.STRIKETHROUGH,
    "code": RichTag.CODE,
    "pre": RichTag.PRE,
    "spoiler": RichTag.SPOILER,
    "tg-spoiler": RichTag.SPOILER,
    "blockquote": RichTag.BLOCKQUOTE,
}

# The renderer and the catalog check have to admit exactly the same tags, or a translation could
# pass the check and then fail to render. The names live in core; the markup each one becomes is
# the part that belongs here.
assert set(FORMAT_TAG_RICH_TAGS) == STYLE_TAG_NAMES, "the rich mapping and the dialect vocabulary disagree"

# The one source tag whose rich markup keeps raw newlines rather than turning them into `<br/>`.
PRE_FORMAT_TAG = "pre"


def spoiler_markup(attrs: str) -> tuple[str, str]:
    if "tg-spoiler" not in parse_tag_attributes(attrs).get("class", "").split():
        raise MalformedRichFormatTag("span", "class")
    return f"<{RichTag.SPOILER}>", f"</{RichTag.SPOILER}>"


def custom_emoji_format_markup(attrs: str) -> tuple[str, str]:
    if not (emoji_id := parse_tag_attributes(attrs).get("emoji-id")):
        raise MalformedRichFormatTag("tg-emoji", "emoji-id")
    return f'<tg-emoji emoji-id="{escape_attribute(emoji_id)}">', "</tg-emoji>"


def format_tag_markup(tag: str, attrs: str) -> tuple[str, str]:
    """The opening and closing rich-HTML markup a translation-source formatting tag becomes."""
    match tag:
        case "a":
            if not (href := parse_tag_attributes(attrs).get("href")):
                raise MalformedRichFormatTag(tag, "href")
            return anchor_markup(href)
        case "span":
            return spoiler_markup(attrs)
        case "tg-emoji":
            return custom_emoji_format_markup(attrs)
        case _:
            if (rich_tag := FORMAT_TAG_RICH_TAGS.get(tag)) is None:
                raise UnsupportedRichFormatTag(tag)
            return f"<{rich_tag}>", f"</{rich_tag}>"


def check_button_placeholders(substitutions: Mapping[str, RichParams]):
    """Enforce that buttons and `${button_*}` placeholders only ever pair with each other.

    A button is markup that renders as a tappable chip, so the placeholder's name is the only thing
    telling a translator one is coming. Both directions are wrong: a `button_` placeholder filled
    with text renders as text under a name promising a button, and a button landing in any other
    placeholder is a chip nothing in the source string announced.
    """
    for name, value in substitutions.items():
        names_a_button = name.startswith(BUTTON_PLACEHOLDER_PREFIX)
        if names_a_button and not is_button_value(value):
            raise NonButtonInButtonPlaceholder(name, value)
        if is_button_value(value) and not names_a_button:
            raise ButtonOutsideButtonPlaceholder(name)


class RichTemplateRenderer:
    """Walks a tagged string once, turning its literal runs, tags and placeholders into markup.

    *substitutions* being `None` marks the string as stored user content: its `${...}` is text
    somebody typed rather than a placeholder, and a tag the dialect cannot read is dropped and
    counted rather than raised, since nobody can go back and fix a row that is already stored.
    """

    def __init__(self, substitutions: Mapping[str, RichParams] | None):
        self.substitutions = substitutions
        self.pieces: list[str] = []
        # (source tag name, closing markup), innermost last: rich HTML has to nest properly, so
        # a close is only valid against the tag opened most recently.
        self.open_tags: list[tuple[str, str]] = []
        self.dropped: Counter[str] = Counter()

    @property
    def lenient(self) -> bool:
        return self.substitutions is None

    @property
    def inside_pre(self) -> bool:
        return any(tag == PRE_FORMAT_TAG for tag, _ in self.open_tags)

    def literal(self, run: str):
        """Emit a run of source text. Character references are decoded before the run is escaped,
        so an authored `&lt;` reaches the reader as a literal `<` rather than as `&amp;lt;`."""
        self.pieces.append(RichContent(html.unescape(run), inside_pre=self.inside_pre).html)

    def drop(self, reason: str, tag: str):
        """Record a tag that produced no markup. Only reachable in lenient mode."""
        self.dropped[f"{reason}:{tag}"] += 1

    def substitution(self, name: str):
        if self.substitutions is None:
            self.literal(f"${{{name}}}")
            return
        if name not in self.substitutions:
            raise MissingRichPlaceholder(name)
        self.pieces.append(rich_value(self.substitutions[name], inside_pre=self.inside_pre).html)

    def open_tag(self, tag: str, attrs: str):
        try:
            opening, closing = format_tag_markup(tag, attrs)
        except RichTemplateError:
            if not self.lenient:
                raise
            # Tracked with no markup of its own so its closing tag is absorbed rather than read as
            # crossing the tag around it.
            self.drop("unreadable", tag)
            self.open_tags.append((tag, ""))
            return
        self.pieces.append(opening)
        self.open_tags.append((tag, closing))

    def close_tag(self, tag: str):
        if not self.open_tags or self.open_tags[-1][0] != tag:
            if not self.lenient:
                raise UnbalancedRichFormatTag(tag)
            self.drop("unbalanced", tag)
            return
        self.pieces.append(self.open_tags.pop()[1])

    def token(self, token: re.Match[str]):
        if (name := token.group("var")) is not None:
            self.substitution(name)
        elif token.group("close"):
            self.close_tag(token.group("tag"))
        else:
            self.open_tag(token.group("tag"), token.group("attrs"))

    def close_dangling(self):
        """Shut the tags the string left open, innermost first, so the markup is well formed."""
        while self.open_tags:
            tag, closing = self.open_tags.pop()
            if not self.lenient:
                raise UnclosedRichFormatTag(tag)
            if closing:
                self.drop("unclosed", tag)
                self.pieces.append(closing)

    def render(self, source: str) -> RichContent:
        cursor = 0
        for token in TOKEN_RE.finditer(source):
            self.literal(source[cursor : token.start()])
            cursor = token.end()
            self.token(token)
        self.literal(source[cursor:])
        self.close_dangling()
        return RichContent.from_markup("".join(self.pieces))


def render_rich_template(source: str, substitutions: Mapping[str, RichParams]) -> RichContent:
    """Render a translated catalog string into rich content, substituting every `${var}`."""
    check_button_placeholders(substitutions)
    return RichTemplateRenderer(substitutions).render(source)


def render_rich_tags(text: str, *, field: str) -> RichContent:
    """Render a tagged string that came out of the database into rich content.

    The tagged string *is* the stored value, with no copy of the user's original entities kept
    anywhere, so a tag this build cannot read degrades what the owner and every participant see
    with nothing to compare it against. It is dropped and logged rather than raised, and *field*
    names the column so a corrupted row is findable instead of inferred from a complaint.

    Substitution is off: a `${cap}` inside a title or a description is what somebody typed, and
    reading it as a placeholder would break that user's own card.
    """
    renderer = RichTemplateRenderer(None)
    rendered = renderer.render(text)
    for reason_and_tag, dropped in renderer.dropped.items():
        reason, _, tag = reason_and_tag.partition(":")
        log.warning("Stored tagged text did not render", field=field, tag=tag, reason=reason, dropped=dropped)
    return rendered
