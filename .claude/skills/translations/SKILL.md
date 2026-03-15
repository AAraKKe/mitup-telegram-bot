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

## Validating translations

Two checks exist:

- `hatch run dev:validate-ids` — ensures every Python message has an entry in `en.po` (English source vs code)
- `hatch run dev:validate-locales` — ensures every non-English `.po` file contains the same msgids as `en.po`, reporting missing/extra entries per language

## Orchestrating translator agents

Per-language vocabulary rules live at `.claude/agents/translations/<lang_code>.md` (e.g. `de_DE.md`). These are consumed by translator agents — the main agent does not need to read them, only reference their path when spawning agents.

Workflow for adding/syncing translations:
1. Run `hatch run dev:validate-locales` to identify which languages are out of sync and which msgids each one is missing.
2. Spawn one translator agent per affected language (never combine languages in one agent). Include the `validate-locales` output in the prompt so the agent knows what to add.
3. After all agents complete, run `hatch run dev:validate-locales` again — must exit 0.

## CI enforcement

Two CI jobs enforce translation correctness:
- `validate-ids` — runs `hatch run dev:validate-ids`; ensures every message in code has a corresponding entry in `en.po`.
- `validate-locales` — runs `hatch run dev:validate-locales`; ensures all non-English `.po` files are in sync with English. Depends on `build-translations`.
