---
name: views
description: Everything about the view layer in `mitup_bot/views/` — the `MitupView` / `MitupInlineView` / `PaginatedMitupView` dataclasses, the `ButtonConfig` model, the `CalendarKeyboard` date picker, the `.with_context()` / `.with_context_menu()` / `.with_back_button()` / `.with_footnote()` builders, destructive-action callback naming, and the full catalogue of factory functions in `views/factory.py` (main_menu, settings, confirmation, pagination helpers, etc.). Use this skill whenever the work touches a screen, keyboard layout, inline keyboard, confirmation dialog, calendar picker, or any file under `mitup_bot/views/` — and use it *first* to check whether a factory function already fits the screen you're about to build, before writing a view by hand. Covers both the reusable building blocks and the rules that make a view correct.
user-invocable: false
---

# Views

The view layer in `mitup_bot/views/` abstracts Telegram message presentation from handler logic. A view pairs a `FormattedText` description with inline keyboards, and the `.markup` property converts the keyboard to a PTB `InlineKeyboardMarkup` at render time.

When building a new screen, check the **factory catalogue** below *first* — reusing a factory keeps behaviour consistent and avoids re-implementing patterns the project has already standardised. Only drop to manual `MitupView` construction when no factory fits.

## Critical rules

<critical_rules>
  <rule>MUST use the `confirmation_view` factory for any accept/decline dialog. Never build confirm/decline keyboards by hand.</rule>
  <rule>All callbacks involved in a destructive action MUST follow the pattern `DELETE_<DESCRIPTION>` (trigger), `CONFIRM_<DESCRIPTION>` (confirm), `DECLINE_<DESCRIPTION>` (decline). This keeps the flow greppable and consistent across features.</rule>
  <rule>NEVER reimplement date picking. Always use `CalendarKeyboard` from `views/calendar.py`.</rule>
  <rule>Pass `MessageBase.get()` output directly as `description` — never extract `.text` first, as that strips formatting entities.</rule>
  <rule>Telegram limits callback data to 64 bytes. `ButtonConfig` validates this at construction time and will raise if exceeded.</rule>
</critical_rules>

Button-label sourcing (never hardcode, always `ButtonMessages.get(lang=...)` / `.back(lang=...)`) is owned by the `user-facing-text` skill — see there for the full rule and examples.

## Core types

### `MitupView`

A dataclass with `description` (`FormattedText`) and `keyboard` (list of `ButtonRow`). Builder methods mutate in place and return `self` for chaining:

- `with_context(message)` — prepends context text above the main description (use for transient status like success/error feedback).
- `with_context_menu(keyboard)` — appends extra button rows below the main keyboard.
- `with_back_button(text, lang, callback_data)` — appends a single back-navigation row.
- `with_footnote(text)` — appends a footnote (secondary, non-critical info) below the description.

### `MitupInlineView`

Extends `MitupView` with `title`, `inline_description`, and `id` for use as inline query results.

### `PaginatedMitupView`

Use when the flat button list could grow beyond ~8 items:

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

### `CalendarKeyboard`

A self-contained date picker in `views/calendar.py`. Covered by the "never reimplement date picking" rule above.

## Factory catalogue

`views/factory.py` contains stateless functions for common screen types. **Always check this catalogue before building a view manually** — if a factory fits, use it.

The snapshot below describes the factories that exist at the time of writing. **Before you pick one, grep `views/factory.py` for `^def ` to see the current list** — factories get added and renamed over time, and this skill does not track those changes automatically.

| Function | Purpose |
|----------|---------|
| `main_menu_view()` | The bot's main menu |
| `settings_view()` | User settings screen |
| `create_meeting_view()` | Meeting creation prompt |
| `request_information_with_cancel_view()` | Ask user for input with a Cancel button |
| `change_settings_element_view()` | Settings input with Cancel back to settings |
| `edit_meeting_property_view()` | Edit a meeting field with a Back button |
| `edit_meeting_date_view()` | Calendar-based date selection for a meeting |
| `settings_set_language_view()` | Language selection from settings |
| `meeting_set_language_view()` | Language selection for a meeting |
| `confirmation_view()` | Yes/no confirmation dialog — MUST use for any accept/decline flow |
| `options_button()` | Toggle button showing ✅ or 🔴 based on current state |
| `user_button()` | Button representing a user (for kick-out, invitation lists) |
| `reactivation_prompt_view()` | Prompt shown to meeting owner when their inactive meeting is accessed |

All factory functions are stateless and take keyword arguments. Inspect the signature in `factory.py` for the exact parameters — they vary by screen type.

### Example

```python
from mitup_bot.views import factory
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import MeetingMessages

view = factory.confirmation_view(
    lang=user.lang,
    message=MeetingMessages.CONFIRM_DELETE.get(lang=user.lang),
    confirm_callback=cb.CONFIRM_DELETE_MEETING.with_id(meeting_id),
    decline_callback=cb.DECLINE_DELETE_MEETING.with_id(meeting_id),
)
```

## Constructing views manually

When no factory fits, construct `MitupView` directly:

```python
from mitup_bot.views import MitupView, ButtonConfig
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingMessages

view = MitupView(
    description=MeetingMessages.MY_MESSAGE.get(lang=user.lang, title=meeting.title),
    keyboard=[
        [ButtonConfig(text=ButtonMessages.CONFIRM.get(lang=lang), callback_data=cb.MY_CALLBACK.with_id(meeting_id))],
    ],
).with_back_button(ButtonMessages.EDIT, lang, cb.EDIT_MEETING.with_id(meeting_id))
```

## Model-level views

`Meetup` (and similar domain models) expose their own views as properties: `main_view`, `edit_view`, `settings_view`, `inline_view`, `external_view`. For new meeting-related screens, prefer adding a property on `Meetup` following this pattern — it keeps the screen logic next to the data it renders and makes handler sites small (`meeting.main_view(lang=user.lang)` rather than building a view from scratch).
