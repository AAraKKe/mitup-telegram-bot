---
name: messages
description: MessageBase and message class conventions for mitup_bot. Auto-load when adding user-facing strings, using .get() on message enums, or working with message template substitution.
user-invocable: false
---

# Messages

All user-facing strings are defined in `mitup_bot/utils/messages.py` as `StrEnum` members of `MessageBase` subclasses.

## Message classes

| Class | Purpose |
|-------|---------|
| `ButtonMessages` | Button labels (used in `ButtonConfig`) |
| `Messages` | Main menu and general bot descriptions |
| `SettingsMessages` | Settings-related text |
| `MeetingMessages` | Meeting creation, join/leave, edit, delete, invitations |
| `InlineViewMessages` | Inline query result UI text |
| `NotificationMessages` | Meeting deletion and start notifications |
| `Weekday`, `Month`, `MonthShort` | Date formatting |
| `Languages` | Language selection labels |

## `MessageBase.get()`

<critical_rules>
  <rule>NEVER hardcode the `lang` argument (e.g., `lang="en"`). Always derive it from `user.lang` or `meeting.lang`.</rule>
</critical_rules>

The primary access method:

```python
def get(
    self,
    *,
    lang: str = TranslationEngine.FALLBACK_LANG,
    full: bool = True,
    plain: bool = False,
    **kwargs: MessageParams,
) -> str:
```

- `lang` — the language code. Always pass `user.lang` or `meeting.lang`.
- `full=True` — performs full MarkdownV2 sanitization on template substitution values.
- `plain=True` — returns the message without MarkdownV2 sanitization. Use for inline query result descriptions and other plain-text contexts.
- `**kwargs` — template substitution values. Uses Python `string.Template` syntax (`${variable}`).

```python
# Translated, with substitution
MeetingMessages.INVITE.get(lang=user.lang, title=meeting.title)

# Plain text (no MarkdownV2 escaping)
MeetingMessages.DESCRIPTION.get(lang=user.lang, plain=True)
```

## `ButtonMessages.back()`

For back-button labels, use `.back(lang=...)` which prepends the "←" arrow:

```python
ButtonMessages.MAIN_MENU.back(lang=user.lang)  # → "← Main Menu"
```

## Template syntax

Template placeholders use Python's `string.Template` syntax:

```python
# In messages.py
INVITE = "You've been invited to ${title}"

# Usage
MeetingMessages.INVITE.get(lang=lang, title=meeting.title)
```

## Content rules

<critical_rules>
  <rule>Messages must contain semantic content only — no MarkdownV2 escaping or formatting in the enum value.</rule>
  <rule>Callers handle formatting. NEVER put `\*bold\*`, `\_italic\_`, or any MarkdownV2 syntax inside a message value.</rule>
</critical_rules>

The enum value is the English source text and serves as the gettext msgid.

## After adding messages

```bash
hatch run dev:update-locales   # update source language file + rebuild .mo files
```

Then delegate translation to the `translator` agent.
