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

`MessageBase.get()` returns a `FormattedText` — use it directly wherever the view accepts `FormattedText`:

```python
# Get translated FormattedText — pass directly to views
MeetingMessages.SOME_MESSAGE.get(lang=user.lang)

# With template substitution
MeetingMessages.INVITE.get(lang=user.lang, title=meeting.title)

# Pass to with_context — never extract .text first
view.with_context(MeetingMessages.SUCCESS.get(lang=user.lang))
```

Only extract `.text` (via `.get_text()` or `.get(...).text`) when you need a genuine plain string — specifically for `answer_callback_query` alert text where Telegram ignores entities anyway. Use `get_text()` in that case, which raises if entities are present:

```python
await api.answer_callback_query(update, text=MeetingMessages.NOT_FOUND.get_text(lang=lang), show_alert=True)
```

## Inline formatting in messages

Use HTML-like tags inside message strings to express formatting. These are parsed at runtime into Telegram entities — they do not appear in the rendered text:

```python
INVITE_USER_MEETING_NOT_FOUND_ON_CALLBACK = "<b>Meeting Not Found</b>\n\nThe meeting does not exist anymore."
INVITED_BY_USER = "<i>invited by ${user}</i>"
```

Supported tags: `<b>` (bold), `<i>` (italic), `<u>` (underline), `<s>` (strikethrough), `<code>`, `<pre>`, `<spoiler>`. Tags may be nested.

<critical_rules>
  <rule>NEVER use MarkdownV2 syntax (`*bold*`, `_italic_`) in message values. Use `<b>`, `<i>` tags instead.</rule>
  <rule>Template placeholders use `${variable_name}` syntax — not `{variable_name}` or `%s`.</rule>
</critical_rules>

## Button labels

All button labels come from `ButtonMessages`.

<critical_rules>
  <rule>NEVER write button text inline. All button labels must come from `ButtonMessages`.</rule>
</critical_rules>

Button labels are plain text — entities in button text are ignored by Telegram. `.get(lang=...)` returns `FormattedText`, which is accepted by `ButtonConfig` (it strips entities internally). Use `.back(lang=...)` for the "← Label" back-button variant, which returns a plain `str`:

```python
ButtonConfig(text=ButtonMessages.JOIN.get(lang=lang), callback_data=cb.JOIN)
ButtonMessages.MAIN_MENU.back(lang=lang)  # → "← Main Menu" (str)
```

## After adding messages

```bash
hatch run dev:update-locales
```

Then delegate translation of the new strings to the `translator` agent.
