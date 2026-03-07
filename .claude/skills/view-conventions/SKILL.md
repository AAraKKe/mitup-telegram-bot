---
name: view-conventions
description: View layer conventions for mitup_bot. Auto-load when creating or editing views, MitupView, PaginatedMitupView, ButtonConfig, or inline keyboards.
user-invocable: false
---

# View Conventions

The view layer in `mitup_bot/views/` abstracts Telegram message presentation from handler logic. Views pair a `FormattedText` description with inline keyboards.

## Important rules

<critical_rules>
  <rule>MUST use the `confirmation_view` factory for any accept/decline dialog. Never build confirm/decline keyboards by hand.</rule>
  <rule>All callbacks involved in a destructive action MUST follow: `DELETE_<DESCRIPTION>` (trigger), `CONFIRM_<DESCRIPTION>` (confirm), `DECLINE_<DESCRIPTION>` (decline).</rule>
</critical_rules>

## Core types

### `MitupView`

A dataclass with `description` (`FormattedText`) and `keyboard` (list of `ButtonRow`). The `.markup` property converts the keyboard to a PTB `InlineKeyboardMarkup`.

<critical_rules>
  <rule>Pass `MessageBase.get()` output directly as `description` — never extract `.text` first, as that strips formatting entities.</rule>
</critical_rules>

Builder methods modify the view in-place and return `self` for chaining:

- `with_context(message)` — prepends context text above the main description.
- `with_context_menu(keyboard)` — appends extra button rows below the main keyboard.
- `with_back_button(text, lang, callback_data)` — appends a single back-navigation row.
- `with_footnote(text)` — appends a footnote (secondary, non-critical info) below the description.

### `MitupInlineView`

Extends `MitupView` with `title`, `inline_description`, and `id` for use as inline query results.

### `PaginatedMitupView`

Use when the button list could grow beyond ~8 items:

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

<critical_rules>
  <rule>Telegram limits callback data to 64 bytes. `ButtonConfig` validates this at construction time and will raise if exceeded.</rule>
</critical_rules>

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

`Meetup` exposes its own views as properties: `main_view`, `edit_view`, `settings_view`, `inline_view`, `external_view`. For new meeting-related screens, prefer adding a property on `Meetup` following this pattern.

## Button text

<critical_rules>
  <rule>NEVER hardcode button text. All button labels must come from `ButtonMessages` in `mitup_bot/utils/messages.py`. Use `.get(lang=...)` for translated text, or `.back(lang=...)` for the "← Label" back-button variant.</rule>
</critical_rules>
