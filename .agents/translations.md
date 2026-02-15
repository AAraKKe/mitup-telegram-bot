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

## Message content vs formatting

Messages should contain **semantic content only** — not MarkdownV2 escaping or formatting characters. Callers are responsible for adding formatting appropriate to their context:

```python
# Good — plain semantic content
NO_LIMIT_PARTICIPANTS = "No limit"

# Bad — bakes MarkdownV2 escaping into the message
NO_LIMIT_PARTICIPANTS = "\\(No limit\\)"
```

When the same message is used in different contexts, callers wrap it accordingly:

- **MarkdownV2** (meeting messages): `f"\\({MeetingMessages.NO_LIMIT.get(lang=lang)}\\)"` → renders `(No limit)`
- **Plain text** (inline descriptions): `f"({MeetingMessages.NO_LIMIT.get(lang=lang, plain=True)})"` → displays `(No limit)`

Use `.get(plain=True)` when rendering messages in plain-text contexts (e.g. inline query result descriptions, button text) where MarkdownV2 is not supported.

## CI enforcement

The `validate-locales` job runs `hatch run dev:validate-locales` to ensure every message defined in code has a corresponding entry in the English translations file. Missing entries cause the job to fail.
