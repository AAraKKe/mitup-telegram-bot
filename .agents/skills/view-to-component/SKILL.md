---
name: view-to-component
description: Translate a Telegram view from `mitup_bot/views/` into a docs chat showcase (`.mitup-phone` or `.mitup-annotated`). Use this skill whenever a docs page needs to mock up, illustrate, update, or fix a bot screen — whether you're writing a new user-guide section, adding a hero shot, refreshing an existing mockup so it matches the current keyboard, or fixing a button label inside an annotated walkthrough. Covers reading `MitupView` / `PaginatedMitupView` / factory functions, looking up the real button text from `ButtonMessages` in `mitup_bot/utils/messages.py`, mapping keyboard rows to `.mitup-bot-msg__row` / `--2` / `--3`, rendering descriptions inside `.mitup-bot-msg__text`, picking between the phone and annotated wrappers, and applying chat-showcase conventions (the `mitupbot` alias, fictitious user names, English text). Trigger as soon as a request mentions a chat bubble, phone mockup, screen, keyboard, or "show what the user sees in X view" — even if the user only names a view by its factory or handler (e.g. "show the settings screen").
user-invocable: false
---

# View → Component

Source-of-truth pipeline:

```
mitup_bot/views/factory.py          ← which screen
mitup_bot/utils/messages.py         ← exact button labels + description text
mitup_bot/views/mitup_view.py       ← builder semantics (with_*, pagination)
   ↓ translate
docs/assets/stylesheets/mitup-components.css   ← CSS contract
   ↓ render
<div class="mitup-phone">  or  <div class="mitup-annotated">
```

The conversion is mechanical once you've read the factory. This skill is the recipe.

This skill is the **translator**. Complementary skills:

* `views` — the source semantics (what a `MitupView` *is*, factory catalogue).
* `docs-style` — the chrome rules (mitupbot alias, fictitious names, voice, when admonitions vs chat showcases).
* `user-facing-text` — where button labels and message text actually come from.

Load all three when both writing the doc *and* changing the underlying view. This one alone is enough when the view already exists and you're just mirroring it into docs.

---

## 1. Pick the wrapper

| Goal | Use |
|------|-----|
| Hero shot, landing page, animation still, first showcase introducing a feature | `.mitup-phone` |
| User-guide / FAQ walkthrough that labels parts of the UI ("this is the keyboard", "this is the bubble") | `.mitup-annotated` |
| Mentioning a single button inside prose (e.g. "tap *Settings*") | No showcase — use the `.button-like` chip instead |

Never write a chat mockup from scratch with custom HTML. If neither wrapper fits, push back on the requirement before inventing new chrome.

---

## 2. Find the view

Look in this order:

1. **Factory** — `grep '^def ' mitup_bot/views/factory.py` and find the one named after the screen (`settings_view`, `main_menu_view`, `create_meeting_view`, `confirmation_view`, …). Read its body in 20 seconds; that *is* the spec.
2. **Inline `MitupView(`** — when no factory matches, grep the relevant handler under `mitup_bot/handlers/` for `MitupView(` or `PaginatedMitupView(`.
3. **Builder chain** — note `.with_back_button(...)`, `.with_context(...)`, `.with_footnote(...)`, `.with_context_menu(...)` calls on the returned view; each one changes the rendered output (see §6).

If the user asks for a screen by feature name ("the language picker", "the kick-out screen"), grep `views/factory.py` for that word first — most names are descriptive.

---

## 3. Resolve the description

The factory passes `Messages.X` / `MeetingMessages.X` / `SettingsMessages.X` / `NotificationMessages.X` (defined in `mitup_bot/utils/messages.py`) as the description. To render:

1. Open `mitup_bot/utils/messages.py` and copy the **English** value of the constant (the bot is multilingual; docs are English).
2. Substitute `${var}` placeholders with realistic example values. For user names, use the fictitious canon: `Ana`, `Ana Marín`, `Marta`, `Diego`, `Sara`, `Tomás`. Never use real maintainer names.
3. Translate Telegram HTML to doc HTML inside `.mitup-bot-msg__text`:

   | Telegram | Doc HTML |
   |----------|----------|
   | `<b>…</b>` | `<strong>…</strong>` |
   | `<i>…</i>` | `<em>…</em>` |
   | `<u>…</u>` | `<u>…</u>` |
   | `<code>…</code>` | `<code>…</code>` |
   | `\n` (single newline) | `<br/>` |
   | `\n\n` (blank line) | `<br/><br/>` |

If the description is a literal string in the factory (rare, but happens for `create_meeting_view`'s default message), use it verbatim.

---

## 4. Resolve each button label

Every `ButtonConfig.text` is sourced from `ButtonMessages.<NAME>.get(lang=...)` in `mitup_bot/utils/messages.py`. The enum value **already includes the emoji**:

```python
NEW_MEETING = f"{Emojis.NEW_MEETING} New meeting"      # → "➕ New meeting"
SETTINGS    = f"{Emojis.SETTINGS} Settings"            # → "⚙️ Settings"
```

So the `.mitup-key` content is the rendered string, emoji included. Look up the actual `Emojis.<NAME>` value in `libs/core/mitup_bot/emojis.py` if you need the glyph. Never invent the emoji.

**Emojis are raw Unicode glyphs, not Twemoji shortcodes.** Mockups mirror what users see in Telegram, so the glyph in `.mitup-key` must match the glyph the bot actually sends. Do not convert to `:shortcode:` form — shortcodes can render a different image from the real button (e.g. `:heart:` renders as `❤️` but `ButtonMessages.COLLABORATE` is `♥`). This matches the same rule for `.button-like` chips in prose; see the `.button-like` recipe in `docs-style`.

Back-button rule: `view.with_back_button(ButtonMessages.MAIN_MENU, …)` renders as `≪ Main Menu` (the `GO_BACK` glyph is `≪`). The same applies to `ButtonMessages.<X>.back(lang=...)` called inside a factory.

If a button is built with `options_button(...)` from `factory.py`, the label is prefixed with ✅ (true) or 🔴 (false). Pick the state you want to illustrate.

---

## 5. Lay out the keyboard

A `MitupView.keyboard` is `list[list[ButtonConfig]]`. Each outer entry = one chat row; row class depends on its length:

| Buttons in row | Row class |
|----------------|-----------|
| 1 | `<div class="mitup-bot-msg__row">` |
| 2 | `<div class="mitup-bot-msg__row mitup-bot-msg__row--2">` |
| 3 | `<div class="mitup-bot-msg__row mitup-bot-msg__row--3">` |

Each button → `<div class="mitup-key">…label…</div>`.

---

## 6. Apply builder methods

| Builder | Effect on rendered HTML |
|---------|-------------------------|
| `.with_context(text)` | Prepend `text` + `<br/><br/>` at the **top** of `.mitup-bot-msg__text`. |
| `.with_footnote(text)` | Append `<br/><br/>` + `text` at the **bottom** of `.mitup-bot-msg__text`. |
| `.with_back_button(label, lang, cb)` | Append a final 1-column row containing one `.mitup-key` with `≪ <label>`. |
| `.with_context_menu(extra_keyboard)` | Append the extra rows to `.mitup-bot-msg__keyboard` after the main rows, following the same row-class rule from §5. |

If the factory chains several builders, apply them in order — `.with_context` then `.with_back_button` means context text appears first, back button appears last.

---

## 7. `PaginatedMitupView`

`PaginatedMitupView` flattens a single button list into a grid plus a nav row. To render a specific page:

1. **Slice**: `buttons[(page-1)*page_size : page*page_size]` where `page_size = row_size * column_size`.
2. **Group**: chunk the slice into rows of `column_size`. Each row uses the `--N` modifier from §5.
3. **Nav row**: append a final row whose contents depend on the page position (computed automatically from `page_number` vs `total_pages`):

   | Position | Nav row contents |
   |----------|------------------|
   | `UNIQUE` (only one page) | *no nav row* |
   | `FIRST` | `[≫]` |
   | `MIDDLE` | `[≪, ≫]` |
   | `LAST` | `[≪]` |

When in doubt about which page to render, pick `MIDDLE` (page 2 of 3) — it shows the most chrome and is the best teaching example.

---

## 8. Chrome & conventions

Every chat showcase MUST use:

* **Avatar**: `<img src="../../assets/images/brand/mark-256.png" alt="Mitup">` inside `.mitup-avatar`. The `../../` depth is for a page at `docs/user-guide/foo.md` or `docs/faq/foo.md` (which renders to `/user-guide/foo/index.html`, i.e. two levels deep). Adjust the `..` count for deeper nesting. Do NOT use `{{ base_url }}` here: Zensical only processes Jinja inside `overrides/` partials, not in raw HTML embedded in markdown — the literal text passes through unchanged. Never a letter, never an SVG placeholder, never an empty disc. The same image is used in the React animation (`docs/animations/`) via a relative path from that folder.
* **Header**: name `mitupbot` (the real bot handle, lowercase), subtitle `bot · online`. Never `Mitup Bot`, never staging variants.
* **Body wrapper**: `.mitup-phone__body` inside `.mitup-phone`, or `.mitup-annotated__body` inside `.mitup-annotated__chat`.
* **Input bar**: include for `.mitup-phone` (it's part of the bezel). Optional for `.mitup-annotated`.
* **Sender row** inside the bubble: `.mitup-bot-msg__sender` says `mitupbot`.
* **User names**: from the fictitious canon (`Ana`, `Ana Marín`, `Marta`, `Diego`, `Sara`, `Tomás`). Vary across pages. Apply to message senders, "Created by:" lines, participant lists, avatar initials, annotation labels.
* **Language**: English only. Even when illustrating the language picker, render the screen in English.

---

## 9. Worked example: `settings_view()` → annotated showcase

Source (`mitup_bot/views/factory.py`):

```python
def settings_view(*, lang: str, message: str | FormattedText | None = None) -> MitupView:
    return MitupView(
        message or Messages.DEFAULT_SETTINGS_DESCRIPTION.get(lang=lang),
        [
            [ButtonConfig(text=ButtonMessages.LANGUAGE.get(lang=lang), ...),
             ButtonConfig(text=ButtonMessages.TIMEOUT.get(lang=lang), ...)],
            [ButtonConfig(text=ButtonMessages.NOTIFICATIONS.get(lang=lang), ...),
             ButtonConfig(text=ButtonMessages.TIMEZONE.get(lang=lang), ...)],
            [ButtonConfig(text=ButtonMessages.DEFAULT_OPTIONS.get(lang=lang), ...),
             ButtonConfig(text=ButtonMessages.PRIVACY.get(lang=lang), ...)],
            [ButtonConfig(text=ButtonMessages.MAIN_MENU.back(lang=lang), ...)],
        ],
    )
```

Look-ups:

* `Messages.DEFAULT_SETTINGS_DESCRIPTION` = `"Configure MitUp."`
* `ButtonMessages.LANGUAGE` = `"🔣 Language"`, `TIMEOUT` = `"⌛ Timeout"`, `NOTIFICATIONS` = `"⏰ Notifications"`, `TIMEZONE` = `"🌐 Timezone"`, `DEFAULT_OPTIONS` = `"👥 Default Options"`, `PRIVACY` = `"🛡️ Privacy"`, `MAIN_MENU.back(...)` = `"≪ Main Menu"`.

Rendered as an annotated showcase:

```html
<div class="mitup-annotated">
  <div class="mitup-annotated__chat">
    <div class="mitup-chat-header">
      <div class="mitup-chat-header__back">‹</div>
      <div class="mitup-avatar"><img src="../../assets/images/brand/mark-256.png" alt="Mitup"></div>
      <div>
        <div class="mitup-chat-header__name">mitupbot</div>
        <div class="mitup-chat-header__sub">bot · online</div>
      </div>
    </div>
    <div class="mitup-annotated__body">
      <div class="mitup-bot-msg">
        <div class="mitup-bot-msg__content">
          <div class="mitup-bot-msg__sender">mitupbot</div>
          <div class="mitup-bot-msg__text">Configure MitUp.</div>
        </div>
        <div class="mitup-bot-msg__keyboard">
          <div class="mitup-bot-msg__row mitup-bot-msg__row--2">
            <div class="mitup-key">🔣 Language</div>
            <div class="mitup-key">⌛ Timeout</div>
          </div>
          <div class="mitup-bot-msg__row mitup-bot-msg__row--2">
            <div class="mitup-key">⏰ Notifications</div>
            <div class="mitup-key">🌐 Timezone</div>
          </div>
          <div class="mitup-bot-msg__row mitup-bot-msg__row--2">
            <div class="mitup-key">👥 Default Options</div>
            <div class="mitup-key">🛡️ Privacy</div>
          </div>
          <div class="mitup-bot-msg__row">
            <div class="mitup-key">≪ Main Menu</div>
          </div>
        </div>
      </div>
    </div>
  </div>
  <span class="mitup-annotation mitup-annotation--left" style="top: 60px;">
    <span class="mitup-annotation__label">Description</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--right" style="top: 120px;">
    <span class="mitup-annotation__label">Settings options</span>
    <span class="mitup-annotation__line"></span>
  </span>
</div>
```

Note how four Python keyboard rows became four `.mitup-bot-msg__row` elements, three with `--2` (two buttons) and the back row with no modifier (one button). The `MAIN_MENU.back(...)` call became `≪ Main Menu` automatically.

---

## 10. Worked example: `PaginatedMitupView` — language picker, page 1 of 1

`set_language_view` builds a `PaginatedMitupView` with one button per supported language, `column_size = min(n, 3)`, `row_size = ceil(n / cols)`. With 6 languages on page 1: position is `UNIQUE`, so no nav row.

```html
<div class="mitup-bot-msg__keyboard">
  <div class="mitup-bot-msg__row mitup-bot-msg__row--3">
    <div class="mitup-key">🇪🇸 Spanish</div>
    <div class="mitup-key">🇬🇧 English</div>
    <div class="mitup-key">🇩🇪 German</div>
  </div>
  <div class="mitup-bot-msg__row mitup-bot-msg__row--3">
    <div class="mitup-key">🇧🇷 Portuguese</div>
    <div class="mitup-key">🇮🇹 Italian</div>
    <div class="mitup-key">🟦 Galician</div>
  </div>
</div>
```

If a future page were `MIDDLE`, the final row would be:

```html
<div class="mitup-bot-msg__row mitup-bot-msg__row--2">
  <div class="mitup-key">≪</div>
  <div class="mitup-key">≫</div>
</div>
```

---

## 11. Pre-save checklist

Before saving the docs page, walk through:

1. Every `.mitup-key` label matches the **rendered** value of a real `ButtonMessages` member (emoji included).
2. Every `.mitup-bot-msg__row` class matches the actual button count in that row (`--2` for two, `--3` for three, none for one).
3. Header name is `mitupbot`; sender row inside the bubble is `mitupbot`.
4. Any user name is from the fictitious canon, not a real contributor.
5. Description HTML uses doc tags (`<strong>`, `<em>`, `<br/>`) — no `<b>` / `<i>` / raw `\n`.
6. Build the docs to catch markup errors: `uv run mb docs build`.
