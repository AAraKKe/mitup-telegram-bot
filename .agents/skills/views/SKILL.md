---
name: views
description: Everything about the view layer in `libs/telegram/mitup_bot/views/` — the `MitupView` / `MitupInlineView` / `PaginatedMitupView` dataclasses, the `ButtonConfig` model, the `CalendarKeyboard` date picker, the `.with_context()` / `.with_context_menu()` / `.with_back_button()` / `.with_footnote()` builders, destructive-action callback naming, and the full catalogue of factory functions in `views/factory.py` (main_menu, settings, confirmation, pagination helpers, etc.). Use this skill whenever the work touches a screen, keyboard layout, inline keyboard, confirmation dialog, calendar picker, or any file under `libs/telegram/mitup_bot/views/` — and use it *first* to check whether a factory function already fits the screen you're about to build, before writing a view by hand. Covers both the reusable building blocks and the rules that make a view correct.
user-invocable: false
---

# Views

The view layer in `libs/telegram/mitup_bot/views/` abstracts Telegram message presentation from handler logic. A view pairs a `FormattedText` description with inline keyboards, and the `.markup` property converts the keyboard to a PTB `InlineKeyboardMarkup` at render time.

When building a new screen, check the **factory catalogue** below *first* — reusing a factory keeps behaviour consistent and avoids re-implementing patterns the project has already standardised. Only drop to manual `MitupView` construction when no factory fits.

## Critical rules

<critical_rules>
  <rule>MUST use the `confirmation_view` factory for any accept/decline dialog. Never build confirm/decline keyboards by hand.</rule>
  <rule>All callbacks involved in a destructive action MUST follow the pattern `DELETE_<DESCRIPTION>` (trigger), `CONFIRM_<DESCRIPTION>` (confirm), `DECLINE_<DESCRIPTION>` (decline). This keeps the flow greppable and consistent across features.</rule>
  <rule>NEVER reimplement date picking. Always use `CalendarKeyboard` from `views/calendar.py`.</rule>
  <rule>Pass `MessageBase.get()` output directly as `description` — never extract `.text` first, as that strips formatting entities.</rule>
  <rule>Telegram limits callback data to 64 bytes. `ButtonConfig` validates this at construction time and will raise if exceeded.</rule>
  <rule>A proactive message — anything sent to a user outside a button/command they just pressed, i.e. via `api.send_message_to_user` / `send_messages_to_users` (notification DMs, membership/tier changes, group readmission or removal, reminders) — MUST carry a navigation keyboard, at minimum a Main-menu button (`ButtonMessages.MAIN_MENU` + `cb.MAIN_MENU`). Build a `MitupView` for it and pass that view to the send call, never a bare `MessageBase.get(...)`. Reason: a proactive message arrives with no surrounding UI, so a keyboard-less one strands the user with no way back into the bot except typing a command. Reuse a factory in `views/collaborate.py` (e.g. `link_confirmation_view`, `hosts_group_readmitted_view`, `hosts_group_removed_view`) or add one there following the same `.with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)` shape.</rule>
</critical_rules>

Button-label sourcing (never hardcode, always `ButtonMessages.get(lang=...)` / `.back(lang=...)`) is owned by the `user-facing-text` skill — see there for the full rule and examples.

## `RenderContext`

Cross-cutting user/session display state — the acting user's language and whether they are an admin — is carried in a single frozen `RenderContext` (`libs/telegram/mitup_bot/views/context.py`, re-exported from `mitup_bot.views`). It is built once per handler from the acting user (the handler-side builder in `guards` constructs it) and passed as the **first positional argument** to every view factory:

```python
view = factory.settings_view(ctx, message=...)
```

The division of responsibility is the rule to follow when adding or changing a factory:

- **Cross-cutting display state belongs in `RenderContext`.** A concern that would otherwise have to be threaded through many factories and their call sites (language, admin visibility, and future additions of the same kind) is added as a field on `RenderContext` rather than as a new per-factory parameter. This is what keeps a new display concern from churning ~20 call sites.
- **Entity data stays as explicit parameters.** Anything specific to the screen — a meeting, ids, callback data, the message body, dates — remains a named keyword argument on the factory. It never goes on the context.

Button helpers that build a single `ButtonConfig` rather than a full view (e.g. `options_button`, `user_button`) do not take a context.

### Rendering in another language

`RenderContext` is frozen. The rare call sites that must render a screen in a language *other* than the acting user's — showing a meeting in the meeting's own language, or echoing back a language the user just selected — use `ctx.with_lang(other_lang)`, which returns a copy with the language replaced:

```python
view = factory.edit_meeting_property_view(ctx.with_lang(meeting.lang), message=..., meeting_id=meeting.db_id)
```

Everywhere else, pass the context straight through unchanged.

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

A Pydantic model wrapping `text` + one action field. `text` is stored as a plain `str`; an entity-free `FormattedText` is accepted at construction and flattened to its text (entities on button labels are rejected — Telegram buttons cannot render them). Supported action fields (mutually exclusive):

- `callback_data` — triggers a callback query when pressed.
- `url` — opens a web URL when pressed (e.g. linking out to the docs site). Not subject to the 64-byte callback limit.
- `switch_inline_query` — prompts the user to select a chat and opens inline mode.
- `switch_inline_query_current_chat` — opens inline mode in the current chat.

`ButtonConfig` (with the `ButtonRow`/`Keyboard` aliases) lives in `libs/core/mitup_bot/keyboards.py`, not in `views/`: keyboards are persisted as message JSON, so the schema is a wire format that must stay pure data — never add Telegram- or view-dependent behaviour to it, and never change its field names, defaults, or serializers without accounting for rows already stored. Import it from `mitup_bot.keyboards`. Rendering to PTB types stays in views: `to_inline_keyboard_button()` converts one button, `MitupView.markup` the whole keyboard.

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

Most view factories are stateless and take a `RenderContext` as their first positional argument (see the `RenderContext` section above); the remaining, screen-specific parameters are keyword-only. A few (e.g. `broadcast_recipient_view`) render directly from an explicit `lang` instead, similar to the model-driven views below, because they render in a recipient's language rather than the acting user's. Inspect the signature in `factory.py` for the exact parameters — they vary by screen type.

### Example

```python
from mitup_bot.views import factory
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import MeetingMessages

view = factory.confirmation_view(
    ctx,
    message=MeetingMessages.CONFIRM_DELETE.get(lang=ctx.lang),
    confirm_callback_data=cb.CONFIRM_DELETE_MEETING.with_id(meeting_id),
    decline_callback_data=cb.DECLINE_DELETE_MEETING.with_id(meeting_id),
)
```

## Constructing views manually

When no factory fits, construct `MitupView` directly:

```python
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.views import MitupView
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingMessages

view = MitupView(
    description=MeetingMessages.MY_MESSAGE.get(lang=user.lang, title=meeting.title),
    keyboard=[
        [ButtonConfig(text=ButtonMessages.CONFIRM.get(lang=lang), callback_data=cb.MY_CALLBACK.with_id(meeting_id))],
    ],
).with_back_button(ButtonMessages.EDIT, lang, cb.EDIT_MEETING.with_id(meeting_id))
```

## Model-driven views (`views/meeting.py`, `views/meeting_settings.py`)

Screens rendered *from* a domain model — the meeting detail/edit/settings/when/inline screens and the default-meeting-options screen — live in `views/meeting.py` and `views/meeting_settings.py`, not in `factory.py` and **never on the model itself**. Models must not import the view layer; anything returning a `MitupView`/`MitupInlineView`/`Keyboard` belongs in `views/`.

These factories take the model as their **first positional argument** instead of a `RenderContext`, because they render in a language derived from the model (the meeting's own language or its owner's), independent of the acting user:

```python
from mitup_bot.views import meeting as meeting_views

view = meeting_views.view_for(meeting, user, back_button=back_button)
```

For a new meeting-related screen, add a function to `views/meeting.py` following this pattern. `views/meeting.py` also owns `keyboard_for_update`, which picks the keyboard to persist on a stored `Message` (owner vs participant vs inline) — callers pass its result to `Message.from_update` / `Meetup.add_message`.

The rendered message *bodies* for those screens — the meeting detail/inline texts, the participants section, the date/time section, participant names — are plain-text/`FormattedText` builders in `views/meeting_text.py` (`meeting_message`, `inline_message`, `inline_query_message`, `participants_text*`, `participant_name`, …), following the same model-as-first-argument pattern. Models never render user-facing text: the entity rendering layer (`utils/entities.py`) imports telegram at runtime, and `mitup_bot.models` must stay importable without PTB (the migrations Lambda loads model metadata for alembic in a PTB-free image).
