---
name: user-facing-text
description: How to write every user-facing string in mitup_bot — both the copy (tone, voice, anti-patterns, button-label wording) and the technical plumbing in `libs/telegram/mitup_bot/utils/messages.py` (MessageBase subclasses like `ButtonMessages`, `MainMenuMessages`, `MeetingCreationMessages`, `NotificationMessages`; `.get()` / `.get_text()` / `.back()`; `${var}` template substitution; inline `<b>`/`<i>`/`<u>`/`<s>`/`<code>`/`<pre>`/`<spoiler>` formatting tags). Use this skill whenever the work touches *any* user-visible message, button label, alert text, callback-query answer, or notification — whether the request is about wording ("make this friendlier", "rewrite the error"), button text ("rename this button"), structure ("add a new menu string"), or implementation ("how do I substitute a name into this message"). If in doubt, load it — it is the single source of truth for bot copy and for the MessageBase API.
user-invocable: false
---

# User-Facing Text

This skill covers two halves of the same problem — *what* the bot says to users, and *how* those strings are defined, formatted, and rendered. They live together because the rules interact: tone decisions constrain what goes in the `StrEnum` body, and the MessageBase API constrains how the copy can be composed.

## How the bot talks

The bot is **not a chat bot** — it is an app whose UI happens to be a Telegram chat. Write strings the way you'd write copy for a mobile app screen, not the way you'd write a conversation.

- **Friendly without being over the top.** Warm and helpful; not excessively enthusiastic.
- **Direct without being terse.** Clear and concise — no padding, no hedging.
- **Genderless by design.** Never assume the gender of the reader or of anyone named in a message. This is a product-wide rule inherited from the original bot: we never know who is reading. English is usually safe by default, but write with translation in mind: a sentence built around a `${name}`-style placeholder will be restructured in gendered languages, so keep the English focused on the action, not on a role noun next to the name ("${name} was added", never "The user ${name} was added").
- **Social context.** Meetings here are social gatherings with friends, family, and groups — not just work meetings. Examples and prompts should reflect that variety.

### What to avoid

- **Fancy punctuation.** No em dashes (`—`), en dashes (`–`), or ellipsis characters (`…`). Plain periods and commas.
- **Standalone filler affirmations.** Don't open a message with `"Perfect!"`, `"Great!"`, `"Done!"` as a detached opener. If the tone calls for it, integrate it — `"You're in!"` beats `"Great! You have joined the meeting."`.
- **Narrating the obvious.** Don't recap what the button just did. A success message should confirm the new state or point at what's next, not describe the action the user just took.
- **Impersonal passive constructions.** Prefer `"Meeting created"` or `"You're all set"` over `"The meeting has been created"`. Short and active reads more human.

## Fixed brand terms — never reworded, never translated

A small set of words are product identity with exactly one spelling. Use them verbatim in copy: don't paraphrase them, swap in a synonym, or reintroduce the words they replaced. They are also **never translated** — every language keeps them as-is (the `translations` skill enforces this on the locale side).

- **Host / Hosts** — the collective term for people who back the bot on Patreon (always capitalized, singular "Host"). When copy refers to a person who pledges, they are a Host. Never "supporter", "patron", "backer", "member", or "subscriber".
- **Patreon tier display names** — the three paid tiers are **Brewer** (☕️), **Gamemaster** (🎲), and **Commissioner** (🏆). These are the exact names shown on Patreon; never call a tier "Supporter", "Patron", or "Organizer" in copy.

Keep the marketing name out of code identifiers. The internal tier enum members are deliberately neutral — `SupporterLevel.HOST_1` / `HOST_2` / `HOST_3`, resolved to a badge via the `supporter` policy — precisely so a future Patreon rename is a copy-only edit. Put the display name in the `MessageBase` string, never in an identifier, an enum value, or a metric key.

## Where strings live

All user-facing strings are `StrEnum` members of `MessageBase` subclasses in `libs/telegram/mitup_bot/utils/messages.py`.

| Class | Purpose |
|-------|---------|
| `ButtonMessages` | Button labels used by `ButtonConfig` |
| `MainMenuMessages` | Main menu and general bot descriptions |
| `SettingsMessages` | Settings-related text |
| `MeetingCreationMessages`, `MeetingDisplayMessages`, `MeetingJoinMessages`, `MeetingInviteMessages`, `MeetingLifecycleMessages`, `MeetingEdit*Messages` | Meeting creation, join/leave, edit, delete, invitations |
| `InlineQueryMessages` | Inline query result UI text |
| `NotificationMessages` | Meeting deletion and start notifications |
| `Weekday`, `Month`, `MonthShort` | Date formatting |
| `Languages` | Language selection labels |

The list above is illustrative — treat `messages.py` as the source of truth and grep for the actual class before adding a new member, because new classes (or merges between existing ones) happen over time.

## Critical rules

<critical_rules>
  <rule>NEVER hardcode user-facing text in handlers, views, or any other module. Every string shown to a user must resolve through a `MessageBase` member so it is translatable.</rule>
  <rule>NEVER hardcode the `lang` argument to `.get()` / `.get_text()` / `.back()` (e.g., `lang="en"`). Always derive it from `user.lang` or `meeting.lang`.</rule>
  <rule>NEVER write button text inline. All button labels come from `ButtonMessages`.</rule>
  <rule>NEVER extract `.text` from a `FormattedText` result and pass it to `with_context`, `with_footnote`, or any view description — that strips entities. Pass the full `FormattedText`.</rule>
  <rule>NEVER use MarkdownV2 syntax (`*bold*`, `_italic_`) in message values. Use the HTML-like tags listed below.</rule>
  <rule>Template placeholders use `${variable_name}` syntax — not `{variable_name}` or `%s`.</rule>
  <rule>Unclosed inline-formatting tags are silently dropped at runtime — no error is raised. Always close every tag you open.</rule>
  <rule>A short status or label string (e.g. "Enabled") may render in exactly ONE sentence context. Gendered languages must inflect it to agree with what it describes, so reusing it under a second referent makes correct translation impossible. When a new screen needs the same English word, add a new enum member instead of reusing the existing one.</rule>
</critical_rules>

## Rendering strings — `.get()` / `.get_text()` / `.back()`

`MessageBase.get(lang=..., **substitutions)` returns a `FormattedText` with translation, placeholder substitution, and inline formatting applied. Pass it directly wherever a view accepts `FormattedText`:

```python
MeetingCreationMessages.SUCCESS.get(lang=user.lang, title=meeting.title)

# Pass to with_context — never extract .text first
view.with_context(MeetingCreationMessages.SUCCESS.get(lang=user.lang, title=meeting.title))
```

Substitution values accept `str`, `int`, `float`, `None`, or another `FormattedText`. A `FormattedText` substitution preserves its entities at the correct offset, which is how you embed one formatted message inside another:

```python
invited_by = MeetingDisplayMessages.INVITED_BY.get(lang=lang, user=inviter.inline_name)
# invited_by is FormattedText with an italic entity
full_name = render(t"{name} ({invited_by})")  # entities preserved
```

`MessageBase.get_text(lang=..., **substitutions)` returns a plain `str` and raises `ValueError` if the rendered result carries entities. Use it only in plain-text contexts where Telegram ignores entities, such as callback-query alert text:

```python
await api.answer_callback_query(
    update,
    text=MeetingInviteMessages.MEETING_NOT_FOUND.get_text(lang=lang),
    show_alert=True,
)
```

`ButtonMessages.back(lang=...)` returns a plain `str` with a `"≪ "` arrow prepended, for the back-button variant. Button labels don't render entities, so the plain-string return type is intentional:

```python
ButtonMessages.MAIN_MENU.back(lang=user.lang)  # → "≪ Main Menu"
```

## Inline formatting in message bodies

Embed formatting with HTML-like tags; `parse_format_tags` converts them to `MessageEntity` objects at render time.

```python
MEETING_NOT_FOUND = (
    "<b>Meeting Not Found</b>\n\nThe meeting you are trying to invite someone to does not exist anymore."
)
INVITED_BY = "<i>invited by ${user}</i>"
```

Supported tags: `<b>`, `<i>`, `<u>`, `<s>`, `<code>`, `<pre>`, `<spoiler>`. Tags may be arbitrarily nested. See the critical rules above for the "close every tag" constraint.

## Button labels

Button labels are plain text — Telegram ignores entities on buttons. `.get(lang=...)` returns `FormattedText`; `ButtonConfig` validates that the `FormattedText` carries no entities and unwraps it to plain text — since button-label values never use formatting tags, this is safe in practice, but a labeled value with entities raises a `ValueError` rather than being silently stripped. For the "≪ Label" back-button variant, use `.back(lang=...)`, which is already plain `str`:

```python
ButtonConfig(text=ButtonMessages.JOIN.get_text(lang=lang), callback_data=cb.JOIN)
ButtonMessages.MAIN_MENU.back(lang=lang)  # → "≪ Main Menu"
```

## After adding or editing strings

```bash
uv run mb locales sync   # update source language file + rebuild .mo files
```

Then delegate translation of the new or changed strings to the `translator` agent. See the `translations` skill for the full locale / Crowdin workflow; this skill owns the strings themselves, not the translation pipeline.
