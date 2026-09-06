from collections.abc import Sequence
from dataclasses import dataclass
from itertools import batched
from math import ceil
from typing import Any, Self, overload

from mitup_bot.callback_data import CallbackData
from mitup_bot.keyboards import ButtonConfig, ButtonRow, Keyboard
from mitup_bot.utils import ButtonMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.entities import FormattedText
from mitup_bot.utils.rich_message import (
    RichContent,
    RichDocument,
    RichMessagePayload,
    RichPhoto,
    as_rich_content,
    carries_custom_emoji_markup,
    horizontal_rule_content,
    without_custom_emoji_content,
)


class MitupView:
    """A screen: the whole rich body, the files it carries, and the button rows that close it.

    `message` is everything the reader sees; `menu` is the rows at the bottom, which stay a
    `Keyboard` because that is the shape persisted on a stored `Message`.
    """

    def __init__(
        self,
        message: RichContent,
        menu: Keyboard | None = None,
        document: RichDocument | None = None,
        photos: Sequence[RichPhoto] = (),
    ):
        self.message = message
        self.menu: Keyboard = menu if menu is not None else []
        self.document = document
        self.photos = tuple(photos)

    @property
    def carries_custom_emoji(self) -> bool:
        """Whether the body holds custom emoji, which Telegram refuses from a bot whose owner has
        no Premium."""
        return carries_custom_emoji_markup(self.message.html)

    def rich_message(self, *, without_custom_emoji: bool = False, inline_addressed: bool = False) -> RichMessagePayload:
        """Render the view into the rich message it is sent as: body, document then menu, one html
        content.

        This is where a view becomes an outbound payload. The whole message is here, so a caller
        sending it needs nothing beside it. An inline-addressed payload carries the menu as a
        classic keyboard beside the body instead of inside it: see `classic_markup` for why.
        """
        body = without_custom_emoji_content(self.message) if without_custom_emoji else self.message
        return RichMessagePayload.from_content(
            body, self.menu, self.document, self.photos, inline_addressed=inline_addressed
        )

    def with_context(self, message: RichContent | str) -> Self:
        """Prepend *message* to the body, cut off from what it comments on by a divider line.

        The context is transient status about the body below it. Nothing gives way to fit: a long
        rich message is folded behind a "Show more" control by the client rather than refused, and
        the one length that is a hard limit is checked against the wire ceiling on the way out.
        """
        self.message = self.message.prepend(as_rich_content(message).append(horizontal_rule_content()))
        return self

    def with_footnote(self, text: RichContent | str) -> Self:
        """Append a footnote at the end of the view's body."""
        self.message = self.message.append("\n\n").append(text)
        return self

    def with_context_menu(self, keyboard: Keyboard) -> Self:
        """Add rows below the view's menu. This can be used to add back buttons to views"""
        self.menu = self.menu + keyboard
        return self

    def with_back_button(self, text: ButtonMessages, lang: str, callback_data: CallbackData) -> Self:
        """Add a back button to the view"""
        back_text = text.back(lang=lang)
        self.menu = self.menu + [[ButtonConfig(text=back_text, callback_data=callback_data)]]
        return self

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, self.__class__):
            return NotImplemented
        return (
            self.message == other.message
            and self.menu == other.menu
            and self.document == other.document
            and self.photos == other.photos
        )

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(message={self.message!r}, menu={self.menu!r}, "
            f"document={self.document!r}, photos={self.photos!r})"
        )


class MitupInlineView(MitupView):
    """
    MitupView that represent an inline view with a title and an id.

    Intended to be used as the representation of a meeting when using inline queries.
    """

    def __init__(
        self,
        *,
        message: RichContent,
        menu: Keyboard | None = None,
        photos: Sequence[RichPhoto] = (),
        title: str | FormattedText,
        inline_description: str | FormattedText,
        id: str,
    ):
        super().__init__(message, menu, photos=photos)
        self.title = title if isinstance(title, str) else title.text
        self.inline_description = inline_description if isinstance(inline_description, str) else inline_description.text
        self.id = id

    def inline_result(self) -> dict[str, Any]:
        """Render the view as one article result of an answered inline query.

        `title` and `description` are what the picker lists; `input_message_content` is the
        message the pick sends. The buttons ride on the result as a classic keyboard rather than
        inside the rich content: see `classic_markup` for the wire-level reason.
        """
        payload = self.rich_message(inline_addressed=True)
        result = {
            "type": "article",
            "id": self.id,
            "title": self.title,
            "description": self.inline_description,
            "input_message_content": {"rich_message": payload.to_api_dict()},
        }
        if payload.reply_markup is not None:
            result["reply_markup"] = payload.reply_markup
        return result

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, MitupInlineView):
            return NotImplemented
        return (
            self.message == other.message
            and self.menu == other.menu
            and self.title == other.title
            and self.inline_description == other.inline_description
            and self.id == other.id
        )


@dataclass
class InlineResultsButton:
    """Button shown above inline query results.

    Abstracts the Telegram `InlineQueryResultsButton` so handlers never
    depend on the PTB type directly.
    """

    text: str
    start_parameter: str | None = None


def arrange_in_grid(buttons: list[ButtonConfig], column_size: int) -> list[ButtonRow]:
    return [list(row) for row in batched(buttons, column_size, strict=False)]


class GridMitupView(MitupView):
    """
    A non-paginated view that arranges a flat button list into a grid.

    Use this instead of `PaginatedMitupView` when all buttons fit on a single
    screen and pagination is never needed — e.g. a fixed-size language picker.
    The number of rows grows with the button count; only the column count is
    specified by the caller.
    """

    def __init__(
        self,
        *,
        message: RichContent,
        buttons: list[ButtonConfig],
        column_size: int = 2,
    ):
        super().__init__(message, arrange_in_grid(buttons, column_size))


class PaginatedMitupView(MitupView):
    """One page of a long list: either a grid of buttons, or rich sections under *message*.

    `navigation_callback_data` is mandatory as soon as the list spans more than one page.
    """

    DEFAULT_ROW_SIZE = 2
    DEFAULT_COLUMN_SIZE = 2
    DEFAULT_PAGE_SIZE = DEFAULT_ROW_SIZE * DEFAULT_COLUMN_SIZE

    @classmethod
    def clamp_page(cls, page_number: int, item_count: int, page_size: int = DEFAULT_PAGE_SIZE) -> int:
        """Clamp a requested page into the valid range for `item_count` items.

        Callback data can carry a stale page (e.g. the last item of the last page was deleted, or
        the list shrank since the button was rendered), so handlers clamp the requested page
        before building the view instead of failing on an out-of-range position.
        """
        total_pages = max(1, ceil(item_count / page_size))
        return max(1, min(page_number, total_pages))

    @overload
    def __init__(
        self,
        *,
        message: RichContent,
        buttons: list[ButtonConfig],
        page_number: int,
        navigation_callback_data: cb.CallbackData | None = None,
        row_size: int = DEFAULT_ROW_SIZE,
        column_size: int = DEFAULT_COLUMN_SIZE,
    ): ...

    @overload
    def __init__(
        self,
        *,
        message: RichContent,
        sections: list[RichContent],
        page_size: int,
        page_number: int,
        navigation_callback_data: cb.CallbackData | None = None,
    ): ...

    def __init__(
        self,
        *,
        message: RichContent,
        buttons: list[ButtonConfig] | None = None,
        sections: list[RichContent] | None = None,
        page_number: int,
        navigation_callback_data: cb.CallbackData | None = None,
        page_size: int | None = None,
        row_size: int | None = None,
        column_size: int | None = None,
    ):
        if (buttons is None) == (sections is None):
            raise ValueError("A page holds either buttons or sections")
        if sections is not None and (row_size is not None or column_size is not None):
            raise ValueError("Sections are sized by page_size alone")
        if buttons is not None and page_size is not None:
            raise ValueError("Buttons are sized by row_size and column_size")

        self.row_size = self.DEFAULT_ROW_SIZE if row_size is None else row_size
        self.column_size = self.DEFAULT_COLUMN_SIZE if column_size is None else column_size
        self.page_size = self.row_size * self.column_size if page_size is None else page_size
        self.page_number = page_number
        self.buttons = buttons if buttons is not None else []
        self.sections = sections if sections is not None else []
        item_count = len(self.buttons) if buttons is not None else len(self.sections)
        # Specify type, ty does nto deal well yet with generic protocols (implemented in ceil typing)
        self.total_pages: int = ceil(item_count / self.page_size)

        if self.page_number <= 0 or self.page_number > self.total_pages:
            raise ValueError("Invalid paginated position")
        super().__init__(self.__page_body(message), self.__page_menu(navigation_callback_data))

    def page_slice[Item](self, items: list[Item]) -> list[Item]:
        first = (self.page_number - 1) * self.page_size
        return items[first : first + self.page_size]

    def __page_body(self, message: RichContent) -> RichContent:
        return RichContent.join(horizontal_rule_content(), [message, *self.page_slice(self.sections)])

    def __page_menu(self, navigation_callback_data: CallbackData | None) -> list[ButtonRow]:
        keyboard = arrange_in_grid(self.page_slice(self.buttons), self.column_size)
        if self.total_pages > 1:
            if navigation_callback_data is None:
                raise ValueError("navigation_callback_data is required when there are more than one page")
            keyboard += [self.__navigation_row(navigation_callback_data)]
        return keyboard

    def __navigation_row(self, navigation_callback_data: CallbackData) -> ButtonRow:
        """The arrows a multi-page view closes on. An arrow with no page left to reach is omitted."""
        row: ButtonRow = []
        if self.page_number > 1:
            row.append(
                ButtonConfig(
                    text=ButtonMessages.GO_BACK,
                    callback_data=navigation_callback_data.with_id(self.page_number - 1),
                )
            )
        if self.page_number < self.total_pages:
            row.append(
                ButtonConfig(
                    text=ButtonMessages.GO_FORWARD,
                    callback_data=navigation_callback_data.with_id(self.page_number + 1),
                )
            )
        return row
