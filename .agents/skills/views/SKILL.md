---
name: views
description: Everything about the view layer in `libs/telegram/mitup_bot/views/`: the `MitupView` / `MitupInlineView` / `PaginatedMitupView` dataclasses, the `ButtonConfig` model, the `Calendar` date picker, the `.with_context()` / `.with_context_menu()` / `.with_back_button()` / `.with_footnote()` builders, destructive-action callback naming, and the full catalogue of factory functions in `views/factory.py` (main_menu, settings, confirmation, pagination helpers, etc.). Use this skill whenever the work touches a screen, keyboard layout, inline keyboard, confirmation dialog, calendar picker, or any file under `libs/telegram/mitup_bot/views/`, and use it *first* to check whether a factory function already fits the screen you're about to build, before writing a view by hand. Covers both the reusable building blocks and the rules that make a view correct.
user-invocable: false
---

# Views

The view layer in `libs/telegram/mitup_bot/views/` abstracts Telegram message presentation from handler logic. A view pairs a `RichContent` message with the button rows closing it and an optional file it carries, and `.rich_message()` renders all three into the one `RichMessagePayload` a send or edit puts on the wire.

When building a new screen, check the **factory catalogue** below *first* — reusing a factory keeps behaviour consistent and avoids re-implementing patterns the project has already standardised. Only drop to manual `MitupView` construction when no factory fits.

## Critical rules

<critical_rules>
  <rule>MUST use the `confirmation_view` factory for any accept/decline dialog. Never build confirm/decline keyboards by hand.</rule>
  <rule>All callbacks involved in a destructive action MUST follow the pattern `DELETE_<DESCRIPTION>` (trigger), `CONFIRM_<DESCRIPTION>` (confirm), `DECLINE_<DESCRIPTION>` (decline). This keeps the flow greppable and consistent across features.</rule>
  <rule>NEVER reimplement date picking. Always use `Calendar` from `views/calendar.py`.</rule>
  <rule>Pass `MessageBase.rich()` output directly as `description`, never flattening it to a string first, since that strips the formatting the message carries.</rule>
  <rule>Telegram limits callback data to 64 bytes. `ButtonConfig` validates this at construction time and will raise if exceeded.</rule>
  <rule>A proactive message, meaning anything sent to a user outside a button/command they just pressed, i.e. via `api.send_message_to_user` / `send_messages_to_users` (notification DMs, membership/tier changes, group readmission or removal, reminders), MUST carry a navigation keyboard, at minimum a Main-menu button (`ButtonMessages.MAIN_MENU` + `cb.MAIN_MENU`). Build a `MitupView` for it and pass that view to the send call, never a bare `MessageBase.rich(...)`. Reason: a proactive message arrives with no surrounding UI, so a keyboard-less one strands the user with no way back into the bot except typing a command. Reuse a factory in `views/collaborate.py` (e.g. `link_confirmation_view`, `hosts_group_readmitted_view`, `hosts_group_removed_view`) or add one there following the same `.with_back_button(ButtonMessages.MAIN_MENU, lang, cb.MAIN_MENU)` shape.</rule>
</critical_rules>

Button-label sourcing (never hardcode, always `ButtonMessages.text(lang=...)` / `.back(lang=...)`) is owned by the `user-facing-text` skill; see there for the full rule and examples.

## `RenderContext`

Cross-cutting user/session display state — the acting user's language and whether they are an admin — is carried in a single frozen `RenderContext` (`libs/telegram/mitup_bot/views/context.py`, re-exported from `mitup_bot.views`). It is built once per handler from the acting user (the handler-side builder in `guards` constructs it) and passed as the **first positional argument** to every view factory:

```python
view = factory.settings_view(ctx, user)
```

The division of responsibility is the rule to follow when adding or changing a factory:

- **Cross-cutting display state belongs in `RenderContext`.** A concern that would otherwise have to be threaded through many factories and their call sites (language, admin visibility, and future additions of the same kind) is added as a field on `RenderContext` rather than as a new per-factory parameter. This is what keeps a new display concern from churning ~20 call sites.
- **Entity data stays as explicit parameters.** Anything specific to the screen — a meeting, ids, callback data, the message body, dates — remains a named keyword argument on the factory. It never goes on the context.

Button helpers that build a single `ButtonConfig` rather than a full view (e.g. `toggle_chip`) do not take a context.

### Rendering in another language

`RenderContext` is frozen. The rare call sites that must render a screen in a language *other* than the acting user's, such as echoing back a language the user just picked, use `ctx.with_lang(other_lang)`, which returns a copy with the language replaced:

```python
view = factory.settings_view(ctx.with_lang(new_language), user)
```

Everywhere else, pass the context straight through unchanged.

## Core types

### `MitupView`

Carries `message` (`RichContent`), `menu` (list of `ButtonRow`) and `document` (an optional `RichDocument`, the file the message brings with it). Builder methods mutate in place and return `self` for chaining:

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

A Pydantic model wrapping `text` + one action field. `text` is a plain `str` and only a `str`: Telegram buttons render no formatting, so a label is rendered with `.text(lang=...)` before it reaches a button. Supported action fields (mutually exclusive):

- `callback_data` — triggers a callback query when pressed.
- `url` — opens a web URL when pressed (e.g. linking out to the docs site). Not subject to the 64-byte callback limit.
- `switch_inline_query` — prompts the user to select a chat and opens inline mode.
- `switch_inline_query_current_chat` — opens inline mode in the current chat.

`ButtonConfig` (with the `ButtonRow`/`Keyboard` aliases) lives in `libs/core/mitup_bot/keyboards.py`, not in `views/`: keyboards are persisted as message JSON, so the schema is a wire format that must stay pure data: never add Telegram- or view-dependent behaviour to it, and never change its field names, defaults, or serializers without accounting for rows already stored. Import it from `mitup_bot.keyboards`. Rendering stays outside it: a button becomes its `<tg-button>` markup in `mitup_bot.utils.rich_message`, and `MitupView.rich_message()` closes the message with the whole keyboard.

### `Calendar`

A self-contained date picker in `views/calendar.py`: it renders as rich content (a table of
tappable day cells plus month and year navigation rows), so it composes into a screen's body via
`.content`. Covered by the "never reimplement date picking" rule above.

## Screen anatomy

Every screen is built from the same few shapes, so a reader recognises a title, a section and an action wherever they are:

- **Screen title**: `RichTag.H2`, one per screen (the meeting title, "❓ Help", "🛡️ Privacy", "⚙️ Settings"). The main menu alone titles itself with `RichTag.H1`, as the bot's front page. No other heading level: clients draw h3 and below barely larger than body text.
- **Section**: the bold glyph line from `views/sections.py` (`section_header` / `card_section`), never a heading tag. A chip that acts on the whole section rides that line after a space (the settings card, the privacy screen); the explanation or values follow on the line below.
- **Blocks** are joined with `horizontal_rule_content()`; block elements (rules, headings, lists, footers, button rows) draw their own margins, so never put a blank line next to one.
- **Actions**: the screen's one call to action is a full-width `style="primary"` row in the body; navigation closes the screen as its `menu`; a chip inside a sentence is that sentence's noun (the help screen's channels), and a chip on a section line is a short verb. Rows hold at most two chips, since the client sizes each chip to its label and a three-chip row reads uneven on a phone and overflows in longer languages.

## Factory catalogue

`views/factory.py` contains stateless functions for common screen types. **Always check this catalogue before building a view manually** — if a factory fits, use it.

The snapshot below describes the factories that exist at the time of writing. **Before you pick one, grep `views/factory.py` for `^def ` to see the current list** — factories get added and renamed over time, and this skill does not track those changes automatically.

| Function | Purpose |
|----------|---------|
| `main_menu_view()` | The bot's main menu |
| `settings_view()` | The user's settings card: every setting is read and changed in place |
| `create_meeting_view()` | Meeting creation prompt |
| `request_information_with_cancel_view()` | Ask user for input with a Cancel button |
| `change_settings_element_view()` | Settings input with Cancel back to settings |
| `language_grid_content()` | The language grid as content, the current language accented |
| `confirmation_view()` | Yes/no confirmation dialog — MUST use for any accept/decline flow |
| `toggle_chip()` | Inline state chip for a boolean setting: green Enabled / red Disabled, tap flips |
| `reactivation_prompt_view()` | Prompt shown to meeting owner when their inactive meeting is accessed |

Most view factories are stateless and take a `RenderContext` as their first positional argument (see the `RenderContext` section above); the remaining, screen-specific parameters are keyword-only. A few (e.g. `broadcast_recipient_view`) render directly from an explicit `lang` instead, similar to the model-driven views below, because they render in a recipient's language rather than the acting user's. Inspect the signature in `factory.py` for the exact parameters — they vary by screen type.

### Example

```python
from mitup_bot.views import factory
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import MeetingMessages

view = factory.confirmation_view(
    ctx,
    message=MeetingMessages.CONFIRM_DELETE.rich(lang=ctx.lang),
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
    description=MeetingMessages.MY_MESSAGE.rich(lang=user.lang, title=meeting.title),
    keyboard=[
        [ButtonConfig(text=ButtonMessages.CONFIRM.text(lang=lang), callback_data=cb.MY_CALLBACK.with_id(meeting_id))],
    ],
).with_back_button(ButtonMessages.EDIT, lang, cb.EDIT_MEETING.with_id(meeting_id))
```

## Model-driven views (`views/meeting/`, `views/meeting_settings.py`)

Screens rendered *from* a domain model (the meeting card, its settings and inline screens, and the default-meeting-options screen) live in the `views/meeting/` package and in `views/meeting_settings.py`, not in `factory.py` and **never on the model itself**. Models must not import the view layer; anything returning a `MitupView`/`MitupInlineView`/`Keyboard` belongs in `views/`.

`views/meeting/` holds one module per surface plus the pieces they share, and its `__init__.py` re-exports the screens so `from mitup_bot.views import meeting as meeting_views` reaches all of them:

| Module | Holds |
|--------|-------|
| `controls.py` | The chips and button rows every meeting screen builds from (`meeting_chip`, `join_leave_row`, `maps_row`, `main_menu_back_button`) |
| `owner_card.py` | `owner_view` and the section builders of its body |
| `attendees.py` | The participants section of the owner card, and the attendance questions it asks |
| `shared_card.py` | `external_view`, `inline_view`, `unavailable_inline_view`, `build_inline_keyboard` |
| `settings_card.py` | `settings_view` |
| `audience.py` | `view_for` and `keyboard_for_update`, which pick a screen for whoever is looking |

A meeting has one screen per audience, and the owner's is `owner_view`: reading the meeting and editing it are the same card, so every field carries the `<tg-button>` chip that edits it and the attendee list names who is coming. Its menu holds only what acts on the meeting as a whole (share, settings, delete) and navigation; everything per-field is a chip in the body. `external_view` is the same meeting for someone who does not own it, and `inline_view` is the shared card. `owner_view` fits itself to `MEETING_CARD_BUDGET` by naming fewer guests, which is the only part of the card allowed to give way.

Every section of the owner card is titled, whether or not it holds a value, so the card keeps one shape from an empty meeting to a full one. The title carries the section's glyph, so the row directly under it does not repeat it: the row that offers to set a missing value is a bare chip named by the title above it. A row that has to be told apart from the one above (the end time, the map pin) keeps a glyph of its own.

The meeting's language is picked on the settings card (`settings_view`), one chip per supported language carrying `cb.SET_MEETING_LANGUAGE`, rather than on a screen of its own. The user's own language is picked the same way, on `factory.settings_view`, whose grid comes from `factory.language_grid_content`.

These factories take the model as their **first positional argument** instead of a `RenderContext`, because they render in a language derived from the model (the meeting's own language or its owner's), independent of the acting user:

```python
from mitup_bot.views import meeting as meeting_views

view = meeting_views.view_for(meeting, user, back_button=back_button)
```

For a new meeting-related screen, add a function to the module in `views/meeting/` that owns that surface, following this pattern, and re-export it from the package `__init__.py` if callers outside the package need it. `audience.py` owns `keyboard_for_update`, which picks the keyboard to persist on a stored `Message` (owner vs participant vs inline); callers pass its result to `Message.from_update` / `Meetup.add_message`.

The fragments those screens compose their bodies out of live in `views/meeting_text.py`, following the same model-as-first-argument pattern. Each one is typed by the surface it feeds: `title_content`, `description_content` and `participant_name` return `RichContent` because a card carries their formatting, while `plain_datetime`, `participants_badge` and `inline_query_message` return `str` because the inline picker's title and description are bare Telegram fields with no formatting at all. Models never render user-facing text: the rendering layer imports telegram at runtime, and `mitup_bot.models` must stay importable without PTB (the migrations Lambda loads model metadata for alembic in a PTB-free image).
