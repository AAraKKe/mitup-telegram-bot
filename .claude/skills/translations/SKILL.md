---
name: translations
description: GNU gettext translation conventions. Auto-load when adding user-facing strings, modifying messages.py, or working with locale files.
user-invocable: false
---

# Translations

The bot supports multiple languages via GNU gettext. The list of supported languages is defined in `SUPPORTED_LANGUAGES` in `mitup_bot/translations.py` — check that file for the current set.

## Architecture

- `mitup_bot/translations.py` — `TranslationEngine` wraps gettext with a per-user locale resolver.
- `mitup_bot/utils/messages.py` — All user-facing strings are defined as `StrEnum` members in message classes (`Messages`, `ButtonMessages`, `MeetingMessages`, `SettingsMessages`, `NotificationMessages`). The English text is the enum value and serves as the gettext msgid.
- `mitup_bot/locales/` — Compiled `.mo` files and source `.po` files per language.
- `crowdin.yml` — Configuration for Crowdin, the translation management platform.

## Key rules

1. **Never hardcode user-facing text** in handlers or views. Define it as a member of the appropriate `StrEnum` in `mitup_bot/utils/messages.py`.
2. **English is the source language.** The enum value is the English text and the gettext key.
3. After adding or modifying messages, update the source language file:

```bash
hatch run dev:update-source-language
```

4. Then rebuild the compiled locale files:

```bash
hatch run dev:build-locales
```

Or do both in one step:

```bash
hatch run dev:update-locales
```

## Message content and formatting

Messages use HTML-like tags for inline formatting and `${variable_name}` for template placeholders:

```python
MEETING_TITLE_LABEL = "<b>Title:</b> ${title}"
INVITED_BY_USER = "<i>invited by ${user}</i>"
```

Supported tags: `<b>`, `<i>`, `<u>`, `<s>`, `<code>`, `<pre>`, `<spoiler>`. Tags may be nested.

NEVER use MarkdownV2 syntax (`*bold*`, `_italic_`) — use `<b>`, `<i>` tags instead.
NEVER use raw escaping (`\\(`, `\\)`) — write plain characters.

## CI enforcement

The `validate-locales` job runs `hatch run dev:validate-locales` to ensure every message defined in code has a corresponding entry in the English translations file. Missing entries cause the job to fail.
