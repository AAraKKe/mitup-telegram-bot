# Views

The view layer in `mitup_bot/views/` abstracts Telegram message presentation from handler logic. Views pair MarkdownV2 text with inline keyboards.

## Core types

### `MitupView`

The fundamental unit — a dataclass with `description` (MarkdownV2 string) and `keyboard` (list of `ButtonRow`). The `.markup` property converts the keyboard to a PTB `InlineKeyboardMarkup`.

Builder methods modify the view in-place and return `self` for chaining:

- `with_context(message)` — prepends context text above the main description.
- `with_context_menu(keyboard)` — appends extra button rows below the main keyboard.
- `with_back_button(text, lang, callback_data)` — appends a single back-navigation row.

### `MitupInlineView`

Extends `MitupView` with `title`, `inline_description`, and `id` for use as inline query results. Used when the bot responds to `@botname` queries.

### `PaginatedMitupView`

Handles lists of buttons that exceed a single screen. Splits buttons across pages and adds forward/back navigation automatically:

```python
PaginatedMitupView(
    description=message,
    buttons=all_buttons,    # Flat list of ButtonConfig
    page_number=1,          # 1-indexed
    navigation_callback_data=cb.MY_PAGE_NAV,
    row_size=2,             # Rows per page
    column_size=2,          # Buttons per row
)
```

Use `PaginatedMitupView` when the button list could grow beyond ~8 items.

### `ButtonConfig`

A Pydantic model wrapping `text` + `callback_data` (or `switch_inline_query`). The `.button` property converts to a PTB `InlineKeyboardButton`.

**Critical constraint:** Telegram limits callback data to **64 bytes**. `ButtonConfig` validates this at the Pydantic level — construction fails if the encoded callback data exceeds 64 bytes. This is why `CallbackData` uses a compact `{action};{entity}:{id}` format.

### `CalendarKeyboard`

A self-contained date picker rendered as an inline keyboard (in `calendar.py`). It generates weekday headers, a day grid (marking today with ✅), and month/year navigation buttons. Do not reimplement date picking — use this component.

## Building new screens

### Use the factory for standard patterns

`views/factory.py` contains stateless functions for common screen types. Check if a factory function already covers your case before building a view manually:

- `main_menu_view()` — the bot's main menu
- `settings_view()` — user settings screen
- `create_meeting_view()` — meeting creation prompt
- `request_information_with_cancel_view()` — ask user for input with a Cancel button
- `change_settings_element_view()` — settings input with Cancel back to settings
- `edit_meeting_property_view()` — edit a meeting field with a Back button
- `edit_meeting_date_view()` — calendar-based date selection for a meeting
- `settings_set_language_view()` / `meeting_set_language_view()` — language selection
- `confirmation_view()` — yes/no confirmation dialog
- `options_button()` — toggle button showing ✅ or 🔴 based on current state
- `user_button()` — button representing a user (for kick-out, invitation lists)

### Construct views directly for custom screens

When no factory function fits, construct `MitupView` directly:

```python
from mitup_bot.views import MitupView, ButtonConfig
from mitup_bot.utils import callbacks as cb, ButtonMessages

view = MitupView(
    description=MeetingMessages.MY_MESSAGE.get(lang=user.lang, title=meeting.title),
    keyboard=[
        [ButtonConfig(text=ButtonMessages.CONFIRM.get(lang=lang), callback_data=cb.MY_CALLBACK.with_id(meeting_id))],
    ],
).with_back_button(ButtonMessages.EDIT, lang, cb.EDIT_MEETING.with_id(meeting_id))
```

### Model-level views

`Meetup` exposes its own views as properties: `main_view`, `edit_view`, `settings_view`, `inline_view`, `external_view`. These are generated from the model's current state. If adding a new meeting-related screen, consider adding a property on `Meetup` following this pattern — it keeps view generation close to the data.

## Button text

All button labels come from `ButtonMessages` in `mitup_bot/utils/messages.py`. Never hardcode button text. Use `.get(lang=...)` for translated text, or `.back(lang=...)` for the "← Label" back-button variant.

## Keyboard layout conventions

- One primary action per row for important buttons.
- Two buttons per row for secondary/navigation actions.
- Back/cancel buttons always go in the last row.
- Use `with_back_button()` for single back navigation — it follows the standard pattern.
