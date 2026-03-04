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
  <rule>NEVER extract `.text` from the result and pass it to `with_context`, `with_footnote`, or any view description — this strips entities. Pass the full `FormattedText`.</rule>
</critical_rules>

Returns a `FormattedText` with translation, placeholder substitution, and formatting applied:

```python
MeetingMessages.INVITE.get(lang=user.lang, title=meeting.title)

# Pass directly to with_context — never extract .text first
view.with_context(MeetingMessages.SUCCESS.get(lang=user.lang))
```

Substitution values accept `str`, `int`, `float`, `None`, or `FormattedText`. A `FormattedText` value preserves its entities at the correct offset — use this to embed one formatted message inside another:

```python
invited_by = MeetingMessages.INVITED_BY_USER.get(lang=lang, user=inviter.inline_name)
# invited_by is FormattedText with an italic entity
full_name = render(t"{name} ({invited_by})")  # entities preserved
```

## `MessageBase.get_text()`

Use only for plain-text contexts (e.g. callback alert text) where the template has no `<tag>` syntax and no `FormattedText` substitution values. Raises `ValueError` if the result has entities:

```python
await api.answer_callback_query(update, text=MeetingMessages.NOT_FOUND.get_text(lang=lang), show_alert=True)
```

## Inline formatting in messages

Embed formatting with HTML-like tags; `parse_format_tags` converts them to `MessageEntity` objects:

```python
INVITE_USER_MEETING_NOT_FOUND_ON_CALLBACK = "<b>Meeting Not Found</b>\n\nThe meeting ... does not exist anymore."
INVITED_BY_USER = "<i>invited by ${user}</i>"
```

Supported tags: `<b>`, `<i>`, `<u>`, `<s>`, `<code>`, `<pre>`, `<spoiler>`. Tags may be arbitrarily nested.

<critical_rules>
  <rule>Unclosed tags are silently dropped at runtime — no error is raised. Always close every tag you open.</rule>
  <rule>NEVER use MarkdownV2 syntax (`*bold*`, `_italic_`) in message values. Use `<b>`, `<i>` tags instead.</rule>
  <rule>Template placeholders use `${variable_name}` syntax — not `{variable_name}` or `%s`.</rule>
</critical_rules>

## `ButtonMessages.back()`

For back-button labels, use `.back(lang=...)` which prepends the "←" arrow and returns a plain `str` (button labels need no entities):

```python
ButtonMessages.MAIN_MENU.back(lang=user.lang)  # → "← Main Menu"
```

## After adding messages

```bash
hatch run dev:update-locales   # update source language file + rebuild .mo files
```

Then delegate translation to the `translator` agent.
