---
name: bot-copy-style
description: Bot interface copy and tone guidelines for mitup_bot. Auto-load when writing or reviewing user-facing messages, button labels, or notification text in the bot interface.
user-invocable: false
---

# Bot Copy Style

## Tone

- **Friendly, not over the top.** The bot is warm and helpful but not excessively enthusiastic.
- **Direct without being terse.** Instructions are clear and concise; no padding.
- **Social context.** Meetings in this bot are social gatherings — with friends, family, groups — not just work meetings. Examples and prompts should reflect this variety.

## Adding messages

All user-facing text lives in `mitup_bot/utils/messages.py` as members of `MessageBase` subclasses:

| Class | Purpose |
|-------|---------|
| `ButtonMessages` | Button labels |
| `Messages` | Main menu and general descriptions |
| `SettingsMessages` | Settings-related text |
| `MeetingMessages` | Meeting creation, join/leave, edit, delete, invitations |
| `InlineViewMessages` | Inline query UI text |
| `NotificationMessages` | Meeting notifications |

<critical_rules>
  <rule>NEVER hardcode user-facing text in handlers, views, or any other module. Always define new strings in the appropriate class in `messages.py`.</rule>
</critical_rules>

## Using messages

```python
# Get translated text
MeetingMessages.SOME_MESSAGE.get(lang=user.lang)

# With template substitution
MeetingMessages.INVITE.get(lang=user.lang, title=meeting.title)

# Plain text (for inline query descriptions, no MarkdownV2)
MeetingMessages.SOME_MESSAGE.get(lang=user.lang, plain=True)
```

## Button labels

All button labels come from `ButtonMessages`.

<critical_rules>
  <rule>NEVER write button text inline. All button labels must come from `ButtonMessages`.</rule>
</critical_rules>

```python
# Standard button
ButtonMessages.JOIN.get(lang=lang)

# Back-button variant (prepends "←")
ButtonMessages.MAIN_MENU.back(lang=lang)
```

## Message content rules

<critical_rules>
  <rule>Messages must contain semantic content only — no MarkdownV2 escaping or formatting characters in the message value itself.</rule>
  <rule>Callers are responsible for formatting: wrap with `sanitize()` or pass `plain=True` as appropriate. Never put formatting inside a message value.</rule>
</critical_rules>

Template placeholders use Python's `string.Template` syntax: `${variable_name}`.

## After adding messages

Run the following to update locale files and rebuild:

```bash
hatch run dev:update-locales
```

Then delegate translation of the new strings to the `translator` agent.
