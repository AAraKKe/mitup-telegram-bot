"""Telegram's rich-HTML dialect: the content type carrying it, and serialization into it.

A rich message carries its formatting as HTML inside the request body rather than as a parallel
entity list, so every span the bot builds has to become a tag here. The tag set is fixed by the
Bot API's "Rich HTML style", and this module is where user-supplied text meets it: plain text is
escaped so none of it can be read as markup, attribute values are escaped for the quotes around
them, and an entity type with no tag raises instead of reaching Telegram stripped of its
formatting.

`RichContent` is that dialect as a value: markup a caller may compose freely because the escaping
question has already been answered for every character in it. Everything that produces content
lives here, so escaping is decided in one module rather than at each call site that builds a
message.
"""

from __future__ import annotations

import datetime as dt
import html
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from telegram import InputFile, MessageEntity

from mitup_bot.keyboards import ButtonConfig, ButtonRow, ButtonStyle, Keyboard
from mitup_bot.utils.entities import (
    TELEGRAM_DATETIME_LINK_TEXT,
    TELEGRAM_DATETIME_LINK_URL,
    EntityDateTime,
    FormattedText,
    utf16_len,
)

# The most characters of visible text a rich message may carry. Telegram counts what it renders,
# not the markup around it, so this is measured with `rich_text_length` rather than on the html.
MAX_RICH_TEXT_LENGTH = 32768

# The document a rich message carries: the id its `<tg-document>` block names, and the multipart
# part the file itself travels in. Telegram resolves the first against the second, so the two are
# separate names rather than one reused twice.
DOCUMENT_MEDIA_ID = "document"
DOCUMENT_ATTACH_NAME = "document_file"


class RichMessageTooLong(ValueError):
    def __init__(self, length: int):
        super().__init__(
            f"Rich message carries {length} characters of text, over the {MAX_RICH_TEXT_LENGTH} Telegram accepts"
        )


class UncarriedRichPhoto(ValueError):
    def __init__(self, media_ids: Sequence[str]):
        named = ", ".join(repr(media_id) for media_id in media_ids)
        super().__init__(f"Rich message html references photos that are not in its media list: {named}")


@dataclass(frozen=True)
class RichDocument:
    """An in-memory file a rich message carries.

    Content and filename travel as one unit so they can never arrive separately: the filename is
    what names the attachment in the chat, and it reaches Telegram in the multipart part rather
    than anywhere in the message body.
    """

    content: bytes
    filename: str


@dataclass(frozen=True)
class RichPhoto:
    """A photo a rich message shows.

    `media_id` is the id an `<img>` block in the html references, and `file_id` is the Telegram
    file it resolves to. The photo's stored `file_unique_id` is used as the media id: it fits the
    64-character limit and uses only the characters an id allows.
    """

    media_id: str
    file_id: str


@dataclass(frozen=True)
class RichMessagePayload:
    """An `InputRichMessage` in its html-content form, ready to be sent as the `rich_message` parameter.

    `skip_entity_detection` stops Telegram from re-scanning the rendered text for links, mentions
    and commands, so the message carries exactly the formatting it was built with and nothing the
    text merely happens to look like.

    A `document` is named twice on the wire: as the `<tg-document>` block sitting in the html, and
    as the media entry resolving it, whose file is uploaded beside the message. A photo is named
    the same two ways: an `<img>` block in the html and the media entry it resolves to. Both halves
    are built from this one value, so a payload never describes a file it does not also send.
    """

    html: str
    skip_entity_detection: bool = True
    document: RichDocument | None = None
    photos: tuple[RichPhoto, ...] = ()
    # The buttons as a classic keyboard, set only for inline-addressed surfaces (see
    # `classic_markup`); every other payload folds them into `html`.
    reply_markup: dict[str, Any] | None = None

    def to_api_dict(self) -> dict[str, Any]:
        content: dict[str, Any] = {"html": self.html, "skip_entity_detection": self.skip_entity_detection}
        document_entries = [] if self.document is None else [document_media_entry()]
        media = document_entries + [photo_media_entry(photo) for photo in self.photos]
        if not media:
            return content
        return content | {"media": media}

    def upload_kwargs(self) -> dict[str, InputFile]:
        """The multipart parts travelling beside the message, keyed by the name `attach://` gives
        each one. PTB uploads an `InputFile` passed as a raw request parameter under its own key."""
        if self.document is None:
            return {}
        return {DOCUMENT_ATTACH_NAME: InputFile(self.document.content, filename=self.document.filename)}

    def markup_kwargs(self) -> dict[str, Any]:
        """The classic keyboard travelling beside the message, as the `reply_markup` parameter."""
        if self.reply_markup is None:
            return {}
        return {"reply_markup": self.reply_markup}

    def check_length(self):
        """Raise when this payload carries more text than Telegram accepts.

        Clients fold a long rich message behind a "Show more" control, so length is a wire limit
        rather than a reading problem and nothing here trims content to fit: a card that silently
        lost its closing lines is worse than one that never went out and said why.
        """
        if (length := rich_text_length(self.html)) > MAX_RICH_TEXT_LENGTH:
            raise RichMessageTooLong(length)

    @classmethod
    def from_content(
        cls,
        content: RichContent,
        keyboard: Keyboard | None = None,
        document: RichDocument | None = None,
        photos: Sequence[RichPhoto] = (),
        *,
        inline_addressed: bool = False,
    ) -> RichMessagePayload:
        """Close *content* with the files it carries and the button rows a rich message ends on.

        Every photo the body references must be in *photos*, otherwise the payload would name a file
        it does not send. A photo in *photos* that the body never references is still sent, only not
        shown.

        An inline-addressed payload keeps its buttons out of the html and carries them as a
        classic keyboard instead: see `classic_markup` for why.
        """
        carried = tuple(photos)
        body = content if document is None else content.append(document_content())
        require_carried_photos(body.html, carried)
        if inline_addressed:
            return cls(body.html, document=document, photos=carried, reply_markup=classic_markup(keyboard or []))
        return cls(body.append(closing_keyboard_content(body, keyboard or [])).html, document=document, photos=carried)


class RichTag(StrEnum):
    """The rich-HTML tags whose markup is the bare tag, with no attribute to fill."""

    BOLD = "b"
    ITALIC = "i"
    UNDERLINE = "u"
    STRIKETHROUGH = "s"
    CODE = "code"
    SPOILER = "tg-spoiler"
    BLOCKQUOTE = "blockquote"
    PRE = "pre"
    # Clients draw every heading serif with no font control: h1 titles the main menu, h2 every
    # other screen, and smaller levels read barely larger than body text.
    H1 = "h1"
    H2 = "h2"
    # Small, muted text closing a block: a byline under a title, an attribution under a card.
    FOOTER = "footer"
    # Wrappers for several photos: a collage shows them all at once, a slideshow one at a time.
    COLLAGE = "tg-collage"
    SLIDESHOW = "tg-slideshow"


HORIZONTAL_RULE_MARKUP = "<hr/>"


def horizontal_rule_content() -> RichContent:
    """A full-width divider line, separating one section of a message from the next."""
    return RichContent.from_markup(HORIZONTAL_RULE_MARKUP)


# Entity type → rich-HTML tag, for the entities whose markup is the bare tag. Every other mapped
# type carries an attribute and is resolved in `rich_tags`.
RICH_HTML_TAGS: dict[str, RichTag] = {
    MessageEntity.BOLD: RichTag.BOLD,
    MessageEntity.ITALIC: RichTag.ITALIC,
    MessageEntity.UNDERLINE: RichTag.UNDERLINE,
    MessageEntity.STRIKETHROUGH: RichTag.STRIKETHROUGH,
    MessageEntity.CODE: RichTag.CODE,
    MessageEntity.SPOILER: RichTag.SPOILER,
    MessageEntity.BLOCKQUOTE: RichTag.BLOCKQUOTE,
}


def entity_type_name(entity_type: str) -> str:
    """Return the wire name of an entity type.

    PTB resolves a recognized `type` to a `MessageEntityType` member whose repr is the enum form,
    so without this a diagnostic would name a known type differently than an unrecognized one,
    which is exactly the case the diagnostics exist for.
    """
    return str(entity_type)


class UnsupportedRichEntity(ValueError):
    def __init__(self, entity_type: str):
        super().__init__(f"Message entity type {entity_type_name(entity_type)!r} has no rich-HTML tag")


class MalformedRichEntity(ValueError):
    def __init__(self, entity_type: str, attribute: str):
        super().__init__(
            f"Message entity of type {entity_type_name(entity_type)!r} carries no {attribute!r}, "
            f"which its rich-HTML tag needs"
        )


class UnsupportedRichButton(ValueError):
    def __init__(self, button: ButtonConfig):
        super().__init__(f"Button {button.text!r} names no action a rich-message button can carry")


class UnsupportedClassicButton(ValueError):
    def __init__(self, button: ButtonConfig):
        super().__init__(
            f"Button {button.text!r} is disabled, which a classic keyboard cannot render; "
            "a keyboard bound for an inline-addressed surface must keep every button tappable"
        )


class OverlappingRichSpans(ValueError):
    """Two entities partially overlap, so no arrangement of tags expresses both."""

    def __init__(self, entity: MessageEntity, open_span_end: int):
        super().__init__(
            f"Message entity of type {entity_type_name(entity.type)!r} at offset {entity.offset} reaches "
            f"past the span ending at {open_span_end}, so the two cannot nest"
        )


def escape_text(text: str) -> str:
    """Escape the characters that would otherwise open a tag or a character reference."""
    return html.escape(text, quote=False)


def escape_content(text: str, *, inside_pre: bool) -> str:
    """Escape *text* for rich-HTML content, turning its newlines into `<br/>` outside a `<pre>`.

    The rich parser collapses every bare newline in html content to a single space, so a
    multi-line message arrives as one run-on paragraph unless its newlines become `<br/>`, which
    survives even inside an inline tag such as `<b>` or `<code>`. A `<pre>` block is the exact
    opposite: it keeps a raw newline and swallows a `<br/>`, losing the line break with it.
    """
    escaped = escape_text(text)
    return escaped if inside_pre else escaped.replace("\n", "<br/>")


def escape_attribute(value: str) -> str:
    """Escape a value bound for a double-quoted attribute, quotes included."""
    return html.escape(value, quote=True)


def require_attribute(entity: MessageEntity, attribute: str) -> str:
    if (value := getattr(entity, attribute)) is None:
        raise MalformedRichEntity(entity.type, attribute)
    return value


def anchor_markup(href: str) -> tuple[str, str]:
    return f'<a href="{escape_attribute(href)}">', "</a>"


class RichContent:
    """Rich-HTML content whose escaping is already settled.

    Plain text becomes content through the constructor, which escapes it, so nothing a reader or a
    translator wrote can be read as markup. Markup this module built enters through `from_markup`,
    which escapes nothing, so a tag is never escaped a second time. Composing two pieces only ever
    concatenates their finished markup, which is what makes the distinction hold through an
    arbitrarily deep composition instead of only at the point a string was first wrapped.

    There is deliberately no `__str__`: the string form of rich content is exactly the ambiguity
    this type exists to remove, so reading it out is spelled `.html`.
    """

    __slots__ = ("_html",)

    def __init__(self, text: str = "", *, inside_pre: bool = False):
        self._html = escape_content(text, inside_pre=inside_pre)

    @classmethod
    def from_markup(cls, markup: str) -> RichContent:
        """Adopt finished rich-HTML markup. The caller vouches that its text is already escaped."""
        content = cls()
        content._html = markup
        return content

    @classmethod
    def link(cls, text: str, url: str) -> RichContent:
        opening, closing = anchor_markup(url)
        return cls.from_markup(f"{opening}{escape_content(text, inside_pre=False)}{closing}")

    @classmethod
    def pre(cls, text: str) -> RichContent:
        """A `<pre>` block, whose text keeps the raw newlines the tag preserves."""
        return cls(text, inside_pre=True).wrap(RichTag.PRE)

    @classmethod
    def join(cls, separator: RichContent | str, parts: Iterable[RichContent | str]) -> RichContent:
        joined = as_rich_content(separator).html
        return cls.from_markup(joined.join(as_rich_content(part).html for part in parts))

    @property
    def html(self) -> str:
        return self._html

    @property
    def text(self) -> str:
        """What a reader sees, tags removed. For logs and plain-text surfaces, never for sending."""
        return rich_text(self._html)

    @property
    def is_plain(self) -> bool:
        """Whether this content is its text and nothing else.

        True exactly when the markup is what escaping the text produces, which is the case for
        content built out of unformatted text and for nothing else: every tag, chip and rule
        survives in the markup while `text` drops it, so the two forms part as soon as one is
        there. What a plain-text surface can carry without losing anything.
        """
        return RichContent(self.text).html == self._html

    @property
    def text_length(self) -> int:
        """How many characters a reader sees, which is what Telegram caps a rich message on."""
        return rich_text_length(self._html)

    def append(self, suffix: RichContent | str) -> RichContent:
        return RichContent.from_markup(self._html + as_rich_content(suffix).html)

    def prepend(self, prefix: RichContent | str) -> RichContent:
        return RichContent.from_markup(as_rich_content(prefix).html + self._html)

    def wrap(self, tag: RichTag) -> RichContent:
        """Return this content inside one *tag*. Empty content is returned untouched: an empty
        pair of tags formats nothing and only lengthens the payload."""
        if not self._html:
            return self
        return RichContent.from_markup(f"<{tag}>{self._html}</{tag}>")

    def __bool__(self) -> bool:
        return bool(self._html)

    def __repr__(self) -> str:
        return f"RichContent({self._html!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RichContent):
            return NotImplemented
        return self._html == other._html


def as_rich_content(value: RichContent | str) -> RichContent:
    """Read *value* as content, escaping it when it is a bare string."""
    return value if isinstance(value, RichContent) else RichContent(value)


# Every tag in markup this module produced. Attribute values are escaped for their quotes, `>`
# among them, so no tag can carry one and the match cannot run past its own end.
RICH_TAG_RE = re.compile(r"<[^>]*>")

LINE_BREAK_MARKUP = "<br/>"


def rich_text(markup: str) -> str:
    """The text a reader sees in *markup*, with its tags removed.

    Each `<br/>` and `<hr/>` becomes the line break it draws and character references decode to
    the single character they stand for, so the result reads as the message rather than as its
    source. A list opening and each item's end are the line breaks a list draws, so its items
    project one per line instead of running together, and a table projects one row per line with
    its cells spaced apart.
    """
    text = (
        markup.replace(LINE_BREAK_MARKUP, "\n")
        .replace(HORIZONTAL_RULE_MARKUP, "\n")
        .replace("<ul>", "\n")
        .replace("</li>", "\n")
        .replace("</th></tr>", "\n")
        .replace("</td></tr>", "\n")
        .replace("</th>", " ")
        .replace("</td>", " ")
    )
    return html.unescape(RICH_TAG_RE.sub("", text))


def rich_text_length(markup: str) -> int:
    """How many characters a reader sees in *markup*, tags excluded.

    Telegram caps a rich message on the text it renders rather than on the markup carrying it, so
    the tags have to come off before anything is counted: a card can spend thousands of characters
    on button markup while showing very little.

    This is the one length in the module measured in characters. Entity offsets and lengths are
    measured in UTF-16 code units instead, because Telegram's entity contract defines them that
    way; `utf16_len` serves those and only those.
    """
    return len(rich_text(markup))


def pre_markup(entity: MessageEntity) -> tuple[str, str]:
    """Return `<pre>` markup, nesting a language-tagged `<code>` when the entity names a language."""
    if entity.language is None:
        return "<pre>", "</pre>"
    return f'<pre><code class="language-{escape_attribute(entity.language)}">', "</code></pre>"


def mention_markup(entity: MessageEntity) -> tuple[str, str]:
    if entity.user is None:
        raise MalformedRichEntity(entity.type, "user")
    return anchor_markup(f"tg://user?id={entity.user.id}")


def custom_emoji_markup(entity: MessageEntity) -> tuple[str, str]:
    emoji_id = escape_attribute(require_attribute(entity, "custom_emoji_id"))
    return f'<tg-emoji emoji-id="{emoji_id}">', "</tg-emoji>"


def date_time_attributes(unix_time: dt.datetime, date_time_format: str | None) -> str:
    """The attributes a `<tg-time>` carries, omitting the format when the moment names none."""
    unix = int(unix_time.timestamp())
    if date_time_format is None:
        return f'unix="{unix}"'
    return f'unix="{unix}" format="{escape_attribute(date_time_format)}"'


def date_time_markup(entity: MessageEntity) -> tuple[str, str]:
    if entity.unix_time is None:
        raise MalformedRichEntity(entity.type, "unix_time")
    return f"<tg-time {date_time_attributes(entity.unix_time, entity.date_time_format)}>", "</tg-time>"


def date_time_content(value: EntityDateTime) -> RichContent:
    """A `<tg-time>` chip: its text is what a client that cannot resolve the moment shows instead."""
    attributes = date_time_attributes(value.unix_time, value.date_time_format)
    return RichContent.from_markup(f"<tg-time {attributes}>{escape_content(value.text, inside_pre=False)}</tg-time>")


def rich_tags(entity: MessageEntity, covered_text: str) -> tuple[str, str]:
    """Return the opening and closing markup wrapping *covered_text* for *entity*.

    A `url` entity carries no address of its own: the text it covers is the address, so it becomes
    an anchor pointing at itself.
    """
    if (tag := RICH_HTML_TAGS.get(entity.type)) is not None:
        return f"<{tag}>", f"</{tag}>"
    match entity.type:
        case MessageEntity.PRE:
            return pre_markup(entity)
        case MessageEntity.TEXT_LINK:
            return anchor_markup(require_attribute(entity, "url"))
        case MessageEntity.TEXT_MENTION:
            return mention_markup(entity)
        case MessageEntity.URL:
            return anchor_markup(covered_text)
        case MessageEntity.CUSTOM_EMOJI:
            return custom_emoji_markup(entity)
        case MessageEntity.DATE_TIME:
            return date_time_markup(entity)
        case _:
            raise UnsupportedRichEntity(entity.type)


def decode_span(encoded: bytes, start: int, end: int) -> str:
    """Return the text lying between two UTF-16 offsets of *encoded*, a message in UTF-16.

    Telegram counts entity positions in UTF-16 code units, so cutting the encoded bytes is what
    keeps an emoji earlier in the message from shifting where a span begins.
    """
    return encoded[start * 2 : end * 2].decode("utf-16-le")


# An entity that sits inside no other, paired with the entities that sit inside it.
EntityGroup = tuple[MessageEntity, list[MessageEntity]]


def group_nested_entities(entities: list[MessageEntity]) -> list[EntityGroup]:
    """Pair each entity that sits inside no other with the entities that sit inside it.

    *entities* must be sorted by start offset and then by descending length, which places an entity
    immediately before everything it contains. An entity that starts inside another but ends after
    it fits at neither level, so it is rejected rather than nested where it does not belong.
    """
    groups: list[EntityGroup] = []
    index = 0
    while index < len(entities):
        parent = entities[index]
        parent_end = parent.offset + parent.length
        index += 1
        first_child = index
        while index < len(entities) and entities[index].offset < parent_end:
            child = entities[index]
            if child.offset + child.length > parent_end:
                raise OverlappingRichSpans(child, parent_end)
            index += 1
        groups.append((parent, entities[first_child:index]))
    return groups


def rich_html_between(
    encoded: bytes, entities: list[MessageEntity], start: int, end: int, *, inside_pre: bool = False
) -> str:
    """Build the rich HTML for the text between the *start* and *end* offsets of *encoded*.

    For every entity in that stretch that sits inside no other, this copies the plain text leading
    up to it and then wraps the entity's own text in its pair of tags, filling what goes between
    the tags by calling itself on the entities nested within. The text after the last entity is
    copied the same way, and every copied piece is escaped so that user text cannot become markup.

    *entities* holds only the entities lying inside the stretch, sorted as `group_nested_entities`
    requires. *inside_pre* says the stretch lies within a `<pre>` block, the one place a raw
    newline survives, and it stays true for everything nested deeper in.
    """
    pieces: list[str] = []
    cursor = start
    for entity, children in group_nested_entities(entities):
        entity_end = entity.offset + entity.length
        opening, closing = rich_tags(entity, decode_span(encoded, entity.offset, entity_end))
        interior = rich_html_between(
            encoded, children, entity.offset, entity_end, inside_pre=inside_pre or entity.type == MessageEntity.PRE
        )
        pieces.append(escape_content(decode_span(encoded, cursor, entity.offset), inside_pre=inside_pre))
        pieces.append(f"{opening}{interior}{closing}")
        cursor = entity_end
    pieces.append(escape_content(decode_span(encoded, cursor, end), inside_pre=inside_pre))
    return "".join(pieces)


def formatted_text_to_rich_html(formatted: FormattedText) -> str:
    """Serialize *formatted* into the rich-HTML dialect Telegram parses for rich messages.

    Every character of user content flows through here, which makes this the boundary that keeps
    text from becoming markup. An entity type with no tag raises `UnsupportedRichEntity`: dropping
    it quietly would ship a message whose formatting is wrong with nothing to say so.
    """
    ordered = sorted(formatted.entities, key=lambda entity: (entity.offset, -entity.length))
    return rich_html_between(formatted.text.encode("utf-16-le"), ordered, 0, utf16_len(formatted.text))


def button_action_attributes(button: ButtonConfig) -> str:
    """The attributes naming what *button* does when pressed.

    `ButtonConfig` validates that exactly one action field is set, so the branches are exhaustive:
    reaching the raise means `ButtonConfig` grew an action this serializer does not know.
    """
    if button.callback_data is not None:
        return f'type="callback_data" data="{escape_attribute(str(button.callback_data))}"'
    if button.url is not None:
        return f'type="url" url="{escape_attribute(button.url)}"'
    if button.switch_inline_query is not None:
        return f'type="switch_inline_query" query="{escape_attribute(button.switch_inline_query)}"'
    if button.switch_inline_query_current_chat is not None:
        return (
            'type="switch_inline_query_current_chat" '
            f'query="{escape_attribute(button.switch_inline_query_current_chat)}"'
        )
    raise UnsupportedRichButton(button)


def style_attribute(style: ButtonStyle | None) -> str:
    return f' style="{style}"' if style is not None else ""


def disabled_button_markup(label: str, style: ButtonStyle | None = None) -> str:
    """An inert chip: drawn like a button but answering no tap.

    What a control renders as when it names something instead of offering it: a precondition not
    met yet, the page a reader is already on, or a state the card is reporting. A *style* keeps the
    chip's accent, which is what lets a status chip read as a status rather than as a dead control.
    """
    return f'<tg-button type="disabled"{style_attribute(style)}>{escape_text(label)}</tg-button>'


def button_markup(button: ButtonConfig) -> str:
    """Serialize one button. Its label is content, so it is escaped as text rather than markup.

    A label keeps any bare newline it carries, which the parser collapses to a space: a button
    reads as one line, and `<br/>` is only verified to behave inside the body's inline tags.
    """
    if button.disabled:
        return disabled_button_markup(button.text, button.style)
    attributes = f"{button_action_attributes(button)}{style_attribute(button.style)}"
    return f"<tg-button {attributes}>{escape_text(button.text)}</tg-button>"


def button_row_markup(row: ButtonRow) -> str:
    """Serialize one keyboard row.

    The row carries no `align`: omitting it stretches the buttons to fill the row and split its
    width evenly, which is how a classic inline keyboard row reads.
    """
    return f"<tg-button-row>{''.join(button_markup(button) for button in row)}</tg-button-row>"


def keyboard_markup(keyboard: Keyboard) -> str:
    """Serialize a keyboard into the button rows that close a rich message's content.

    A row holds one to eight buttons, so an empty one is dropped rather than sent as a row Telegram
    would reject.
    """
    return "".join(button_row_markup(row) for row in keyboard if row)


def classic_button(button: ButtonConfig) -> dict[str, Any]:
    """Serialize one button as a classic `InlineKeyboardButton` dict.

    A classic button carries no style, so `ButtonConfig.style` is dropped; the accent only exists
    on the rich rendering. A classic keyboard has no inert state at all, so a disabled button
    raises: dropping it silently would hide the control the caller thinks it rendered, and the
    keyboards bound for classic surfaces are built to keep every button tappable.
    """
    if button.disabled:
        raise UnsupportedClassicButton(button)
    serialized: dict[str, Any] = {"text": button.text}
    if button.callback_data is not None:
        return serialized | {"callback_data": str(button.callback_data)}
    if button.url is not None:
        return serialized | {"url": button.url}
    if button.switch_inline_query is not None:
        return serialized | {"switch_inline_query": button.switch_inline_query}
    if button.switch_inline_query_current_chat is not None:
        return serialized | {"switch_inline_query_current_chat": button.switch_inline_query_current_chat}
    raise UnsupportedRichButton(button)


def classic_markup(keyboard: Keyboard) -> dict[str, Any] | None:
    """Serialize a keyboard as a classic `InlineKeyboardMarkup` dict, or None when it is empty.

    Inline-addressed surfaces (inline query results, and edits addressed by `inline_message_id`)
    carry their buttons this way instead of inside the rich content: Telegram only includes
    `inline_message_id` in `chosen_inline_result` when the sent message has a classic keyboard
    attached, and without that id a freshly shared card can never be claimed or authorized
    (https://github.com/tdlib/telegram-bot-api/issues/895). A classic keyboard renders the same as
    the align-less button rows the rich content would carry, so the fork is invisible to users. A
    disabled button cannot cross the fork and raises in `classic_button`.
    """
    rows = [[classic_button(button) for button in row] for row in keyboard if row]
    if not rows:
        return None
    return {"inline_keyboard": rows}


def disabled_button_content(label: str, style: ButtonStyle | None = None) -> RichContent:
    return RichContent.from_markup(disabled_button_markup(label, style))


def button_content(button: ButtonConfig) -> RichContent:
    return RichContent.from_markup(button_markup(button))


def button_row_content(row: ButtonRow) -> RichContent:
    return RichContent.from_markup(button_row_markup(row))


def keyboard_content(keyboard: Keyboard) -> RichContent:
    return RichContent.from_markup(keyboard_markup(keyboard))


def closing_keyboard_content(body: RichContent, keyboard: Keyboard) -> RichContent:
    """The rows closing a message, behind the divider that separates them from the body above.

    An in-content keyboard sits in the same flow as the text, so without a line above it the first
    row reads as one more paragraph of the message. The line is drawn here, at the single point
    where a keyboard is folded into content, rather than by each screen: every screen wants it, and
    a screen that composed its own would have to know whether its buttons travel in the content at
    all. Nothing to separate means no line: a keyboard that renders to no rows, and a message whose
    whole body is the keyboard, both come back as they are.
    """
    rows = keyboard_content(keyboard)
    if not rows or not body:
        return rows
    return horizontal_rule_content().append(rows)


def unordered_list_content(items: Iterable[RichContent]) -> RichContent:
    """A native bullet list, one item per entry. Renders as a block with the client's list chrome."""
    joined = "".join(f"<li>{item.html}</li>" for item in items)
    return RichContent.from_markup(f"<ul>{joined}</ul>")


def table_content(header: Sequence[RichContent], rows: Sequence[Sequence[RichContent]]) -> RichContent:
    """A table of content cells, the first row its headings. Renders as a full-width block."""
    headings = "".join(f"<th>{cell.html}</th>" for cell in header)
    body = "".join("<tr>" + "".join(f"<td>{cell.html}</td>" for cell in row) + "</tr>" for row in rows)
    return RichContent.from_markup(f"<table><tr>{headings}</tr>{body}</table>")


def map_content(latitude: float, longitude: float, *, zoom: int = 15) -> RichContent:
    """An embedded, tappable map centered on the coordinates. Renders as a full-width block."""
    return RichContent.from_markup(f'<tg-map lat="{latitude}" long="{longitude}" zoom="{zoom}"/>')


def document_markup() -> str:
    """The block a carried file renders as, pointing at the media entry that resolves it."""
    return f'<tg-document src="tg://document?id={DOCUMENT_MEDIA_ID}"></tg-document>'


def document_content() -> RichContent:
    return RichContent.from_markup(document_markup())


def document_media_entry() -> dict[str, Any]:
    """The `InputRichMessageMedia` the block's `tg://document?id=` reference resolves against.

    It names the file rather than carrying it: `attach://` points at the multipart part
    `RichMessagePayload.upload_kwargs` sends, which is where the bytes and the filename go.
    """
    return {"id": DOCUMENT_MEDIA_ID, "media": {"type": "document", "media": f"attach://{DOCUMENT_ATTACH_NAME}"}}


def photo_markup(media_id: str) -> str:
    """The block one photo renders as, pointing at the media entry that resolves it."""
    return f'<img src="tg://photo?id={escape_attribute(media_id)}"/>'


def photo_content(media_id: str) -> RichContent:
    return RichContent.from_markup(photo_markup(media_id))


def collage_content(parts: Iterable[RichContent]) -> RichContent:
    """Photo blocks shown together in one grid."""
    return RichContent.join("", parts).wrap(RichTag.COLLAGE)


def slideshow_content(parts: Iterable[RichContent]) -> RichContent:
    """Photo blocks shown one at a time, the reader swiping between them."""
    return RichContent.join("", parts).wrap(RichTag.SLIDESHOW)


def photo_media_entry(photo: RichPhoto) -> dict[str, Any]:
    """The `InputRichMessageMedia` a `tg://photo?id=` reference resolves to.

    The photo is sent as a Telegram file id rather than as uploaded bytes, because an inline query
    result cannot upload files and a stored id always names a file Telegram already holds.
    """
    return {"id": photo.media_id, "media": {"type": "photo", "media": photo.file_id}}


# Matches every `tg://photo?id=` reference in the html. The id is an escaped attribute value, so
# it holds no `"` and the match ends at the closing quote.
PHOTO_REFERENCE_RE = re.compile(r'tg://photo\?id=([^"]*)"')


def require_carried_photos(markup: str, photos: Sequence[RichPhoto]):
    """Raise when *markup* references a photo that is not in *photos*."""
    carried = {escape_attribute(photo.media_id) for photo in photos}
    if uncarried := [media_id for media_id in PHOTO_REFERENCE_RE.findall(markup) if media_id not in carried]:
        raise UncarriedRichPhoto(uncarried)


# A custom emoji exactly as `custom_emoji_markup` writes it. The id is an escaped attribute value,
# so it can hold no `"` and the match ends at the real closing quote; the tags never nest, so the
# lazy body ends at this emoji's own closing tag. Nothing else in the dialect matches this shape,
# which is what keeps the stripping below off every other tag in the markup.
CUSTOM_EMOJI_MARKUP_RE = re.compile(r'<tg-emoji emoji-id="[^"]*">(.*?)</tg-emoji>', re.DOTALL)


def carries_custom_emoji_markup(markup: str) -> bool:
    """Whether *markup* holds custom emoji, which Telegram refuses from a bot whose owner has no
    Premium."""
    return CUSTOM_EMOJI_MARKUP_RE.search(markup) is not None


def without_custom_emoji_markup(markup: str) -> str:
    """Return *markup* with its custom emoji unwrapped, keeping the glyph each one covered.

    The second attempt Telegram forces when the bot owner's Premium has lapsed. Only the tag comes
    off: the fallback glyph inside it is what the reader sees afterwards, and every other tag in
    the message is left exactly as it was.
    """
    return CUSTOM_EMOJI_MARKUP_RE.sub(r"\1", markup)


def without_custom_emoji_content(content: RichContent) -> RichContent:
    return RichContent.from_markup(without_custom_emoji_markup(content.html))


def datetime_link_content() -> RichContent:
    """Telegram's date & time formatting help link, as the content a message substitutes."""
    return RichContent.link(TELEGRAM_DATETIME_LINK_TEXT, TELEGRAM_DATETIME_LINK_URL)
