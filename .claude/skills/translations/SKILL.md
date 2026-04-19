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

## Relationship to `user-facing-text`

This skill owns the **locale workflow** — gettext, `.po`/`.mo` files, Crowdin, the translator agents, and the CI validation jobs. The rules for how to *author* a string (never hardcoding, `MessageBase` subclasses, `${placeholder}` syntax, `<b>`/`<i>` inline tags, "never MarkdownV2") live in the `user-facing-text` skill. Don't restate them here; just know that once a string is added there, the workflow below takes over.

## Key workflow rules

1. **English is the source language.** The enum value in `messages.py` is both the English text shown to users *and* the gettext msgid.
2. After adding or modifying a string, regenerate the source language file and the compiled locales:

```bash
hatch run dev:update-source-language   # update .pot / en.po from code
hatch run dev:build-locales             # compile .po → .mo

# or, equivalently, both in one step:
hatch run dev:update-locales
```

## Validating translations

Two checks exist:

- `hatch run dev:validate-ids` — ensures every Python message has an entry in `en.po` (English source vs code)
- `hatch run dev:validate-locales` — ensures every non-English `.po` file contains the same msgids as `en.po`, reporting missing/extra entries per language

## Orchestrating translator agents

Per-language vocabulary rules live at `.claude/agents/translations/<lang_code>.md` (e.g. `de_DE.md`). These are consumed by translator agents — the main agent does not need to read them, only reference their path when spawning agents.

Workflow for adding/syncing translations:
1. Run `hatch run dev:validate-locales` to identify which languages are out of sync and which msgids each one is missing.
2. Spawn one translator agent per affected language (never combine languages in one agent). The translator agent has a helper script (`bin/translation_status.py`) that gives it all the information it needs — you don't need to pre-digest the work list. Just tell the agent the language code and what to do.
3. After all agents complete, run `hatch run dev:validate-locales` again — must exit 0.

When English strings have been updated and translations need review, tell the translator agents to use `--review` mode. This compares English text against a git ref and shows what changed.

## CI enforcement

Two CI jobs enforce translation correctness:
- `validate-ids` — runs `hatch run dev:validate-ids`; ensures every message in code has a corresponding entry in `en.po`.
- `validate-locales` — runs `hatch run dev:validate-locales`; ensures all non-English `.po` files are in sync with English. Depends on `build-translations`.
