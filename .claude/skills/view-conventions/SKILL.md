---
name: view-conventions
description: View layer conventions for mitup_bot. Auto-load when creating or editing views, MitupView, PaginatedMitupView, ButtonConfig, or inline keyboards.
user-invocable: false
---

# View Conventions

The view layer in `mitup_bot/views/` abstracts Telegram message presentation from handler logic. Views pair a `FormattedText` description with inline keyboards.

## Important rules

<critical_rules>
  <rule>MUST use the `confirmation_view` factory method whenever creating a view to accept or decline any user choice. This ensures a consistent user experience and centralises confirmation dialog logic.</rule>
  <rule>All callbacks that trigger a destructive action, confirm it, or decline it MUST follow the naming convention: `DELETE_<DESCRIPTION>` (trigger), `CONFIRM_<DESCRIPTION>` (confirm), `DECLINE_<DESCRIPTION>` (decline).</rule>
</critical_rules>

## Core types

### `MitupView`

The fundamental unit — a dataclass with `description` (`FormattedText`) and `keyboard` (list of `ButtonRow`). The `.markup` property converts the keyboard to a PTB `InlineKeyboardMarkup`.

`description` is always a `FormattedText`. Pass `MessageBase.get()` output directly — never extract `.text` first, as that strips formatting entities.

Builder methods modify the view in-place and return `self` for chaining:

- `with_context(message)` — prepends context text above the main description.
- `with_context_menu(keyboard)` — appends extra button rows below the main keyboard.
- `with_back_button(text, lang, callback_data)` — appends a single back-navigation row.

### `MitupInlineView`

Extends `MitupView` with `title`, `inline_description`, and `id` for use as inline query results.

### `PaginatedMitupView`

Handles lists of buttons that exceed a single screen. Use when the button list could grow beyond ~8 items:

```python
PaginatedMitupView(
    description=message,
    buttons=all_buttons,
    page_number=1,
    navigation_callback_data=cb.MY_PAGE_NAV,
    row_size=2,
    column_size=2,
)
```

### `ButtonConfig`

A Pydantic model wrapping `text` + one action field. Supported action fields (mutually exclusive):

- `callback_data` — triggers a callback query when pressed.
- `switch_inline_query` — prompts the user to select a chat and opens inline mode.
- `switch_inline_query_current_chat` — opens inline mode in the current chat.

**Critical constraint:** Telegram limits callback data to **64 bytes**. `ButtonConfig` validates this at the Pydantic level — construction fails if the encoded callback data exceeds 64 bytes.

### `CalendarKeyboard`

A self-contained date picker in `calendar.py`.

<critical_rules>
  <rule>NEVER reimplement date picking. Always use `CalendarKeyboard` from `calendar.py`.</rule>
</critical_rules>

## Constructing views

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

## Model-level views

`Meetup` exposes its own views as properties: `main_view`, `edit_view`, `settings_view`, `inline_view`, `external_view`. If adding a new meeting-related screen, consider adding a property on `Meetup` following this pattern.

## Button text

All button labels come from `ButtonMessages` in `mitup_bot/utils/messages.py`. Use `.get(lang=...)` for translated text, or `.back(lang=...)` for the "← Label" back-button variant.

<critical_rules>
  <rule>NEVER hardcode button text. All button labels must come from `ButtonMessages`.</rule>
</critical_rules>

## Keyboard layout conventions

- One primary action per row for important buttons.
- Two buttons per row for secondary/navigation actions.
- Back/cancel buttons always go in the last row.
- Use `with_back_button()` for single back navigation.
