---
name: translations
description: GNU gettext translation conventions. Auto-load when adding user-facing strings, modifying messages.py, or working with locale files.
user-invocable: false
---

# Translations

The bot supports multiple languages via GNU gettext. The list of supported languages is defined in `SUPPORTED_LANGUAGES` in `libs/core/mitup_bot/translations.py` — check that file for the current set.

## Architecture

- `libs/core/mitup_bot/translations.py` — `TranslationEngine` wraps gettext with a per-user locale resolver.
- `libs/telegram/mitup_bot/utils/messages.py` — All user-facing strings are defined as `StrEnum` members in message classes (`ButtonMessages`, `SettingsMessages`, `NotificationMessages`, `MeetingCreationMessages`, and others). The English text is the enum value and serves as the gettext msgid.
- `libs/core/mitup_bot/locales/` — Compiled `.mo` files and source `.po` files per language.
- `tools/mb/src/mb/crowdin_ops.py` — the Crowdin API sync behind `mb locales push` / `mb locales pull` (see [Crowdin sync](#crowdin-sync)).

## Relationship to `user-facing-text`

This skill owns the **locale workflow** — gettext, `.po`/`.mo` files, Crowdin, the translator agents, and the CI validation jobs. The rules for how to *author* a string (never hardcoding, `MessageBase` subclasses, `${placeholder}` syntax, `<b>`/`<i>` inline tags, "never MarkdownV2") live in the `user-facing-text` skill. Don't restate them here; just know that once a string is added there, the workflow below takes over.

## Key workflow rules

1. **English is the source language.** The enum value in `messages.py` is both the English text shown to users *and* the gettext msgid.
2. After adding or modifying a string, regenerate the source language file and the compiled locales:

```bash
uv run mb locales update-source    # update en.po source catalog from code
uv run mb locales build            # compile .po → .mo

# or, to also drop stale entries and validate in one step:
uv run mb locales sync
```

3. **One physical line per `msgstr`.** PO follows C string-literal rules: adjacent quoted lines
   concatenate with **no separator**, so a msgstr "wrapped" across continuation lines silently
   glues sentences together in production (`"…Mitup Bot!""Elige una opción"` renders as
   `…Mitup Bot!Elige una opción`). Encode every line break as `\n` inside a single quoted string
   and never split a msgstr across lines.

## Fixed brand terms — keep verbatim in every language

A small set of English brand terms must appear **identically in every language** — never translated, transliterated, or localized. When a msgid contains one of these words, the translated `msgstr` must keep the exact same word (translate the sentence around it):

- **Host / Hosts** — the collective term for people who back the bot on Patreon.
- The Patreon tier display names: **Brewer**, **Gamemaster**, **Commissioner**.

These are product identity and appear identically on Patreon; localizing them would break the mapping users see between the bot and their Patreon account. This is a **hard rule** — always restate it to every translator agent you spawn, and it is baked into the per-language glossaries under `.claude/agents/translations/<lang_code>.md`. The internal tier identifiers are kept neutral (`SupporterLevel.HOST_1/HOST_2/HOST_3`) so these display names live only in copy, never in code.

## Genderless copy — product-wide hard rule

The bot never knows the gender of the person reading, nor of the people it names. Every language must avoid gendering **both**:

1. **The user being addressed** — no masculine (or feminine) greetings, participles, or adjectives aimed at "you".
2. **Any person referenced through a placeholder** (`${name}`, `${user}`, `${participant}`) or by a role noun next to one — a masculine participle or article chain around a name placeholder genders whoever's name lands there.

Where the natural translation is gendered, restructure instead: active voice with the bot/we as subject ("Se ha añadido a ${name}", "Engadimos a ${name}", "Removemos ${participant}"), neutral nouns ("la persona invitada", "die eingeladene Person"), or possessive tags ("invitación de ${user}", "convite de ${user}", "ospite di ${user}"). Per-language safe constructions live in each dictionary. Restate this rule to every translator agent you spawn.

## Concept splits every language must preserve

Some concepts are distinct in the product but collapse into one word if translated lazily. Every catalog must keep them apart with two different words (the exact pair is language-specific and lives in each dictionary under `.claude/agents/translations/`):

- **The meeting's location** (the attribute holding a name and/or a pin) vs **the map pin / coordinates** (the Telegram attach feature). English uses "Location" vs "Map pin"; es uses localización vs ubicación; de Ort vs Standort; it luogo vs posizione; gl lugar vs localización. When a string mentions both, it must use both words.
- When instructions reference the Telegram attach-menu item, the word must match what Telegram's own UI calls it in that language.

## Crowdin sync

Crowdin (project `MitupBot 2.0`) is the source of truth for **translated** text; the repo is the source of truth for **English** text. Two `mb` commands sync the sides (both need the `CROWDIN_API_KEY` env var — a Crowdin personal access token; both accept `--dry-run`):

- `uv run mb locales push` — uploads `en.po` (Crowdin diffs revisions server-side with `keep_translations`, so only *modified* strings lose their approval and return to the review queue) and then uploads the repo translation for every string that is **not approved** in Crowdin and differs from Crowdin's current suggestion — both brand-new strings and changed strings whose repo translation was refreshed (AI translations always land in the repo alongside the English change). Reviewers therefore always see the repo's latest text as the newest suggestion; push never competes with an *approved* translation.
- `uv run mb locales pull` — merges **approved** Crowdin translations back into the per-language catalogs (updating changed entries in place and appending newly translated msgids), then recompiles the `.mo` catalogs.

A third command, `mb locales create-mr` (`tools/mb/src/mb/crowdin_mr_ops.py`), is CI-only: it commits a pulled delta and force-pushes the `crowdin-translations` MR branch. CI runs `crowdin-push` on every default-branch push and `crowdin-pull` from an hourly schedule that keeps a single `crowdin-translations` MR open with the approved-vs-main delta (see the `ci-pipeline` skill).

When touching `crowdin_ops.py`, one Crowdin export quirk is load-bearing: without `skipUntranslatedStrings: true`, Crowdin fills every string missing the export filter with the **English source text**, indistinguishable from a real translation.

**Fixing an already-approved translation must go through Crowdin.** `push` never uploads over an approved string and the hourly `pull` merges approved text back into the repo, so a repo-side edit to an approved translation is reverted on the next pull. Either make the edit in Crowdin directly, or un-approve the string there first so the next `push` uploads the repo's fix as the newest suggestion for review. Repo-side fixes are only durable for strings not yet approved in Crowdin.

## Validating translations

Two checks exist:

- `uv run mb locales validate-ids` — ensures every Python message has an entry in `en.po` (English source vs code)
- `uv run mb locales validate` — ensures every non-English `.po` file contains the same msgids as `en.po`, reporting missing/extra entries per language

## Orchestrating translator agents

Per-language vocabulary rules live at `.claude/agents/translations/<lang_code>.md` (e.g. `de_DE.md`). These are consumed by translator agents — the main agent does not need to read them, only reference their path when spawning agents.

Workflow for adding/syncing translations:
1. Run `uv run mb locales validate` to identify which languages are out of sync and which msgids each one is missing.
2. Spawn one translator agent per affected language (never combine languages in one agent). The translator agent has a helper script (`tools/translation_status.py`) that gives it the work list with the English source text for each entry — you don't need to pre-digest the list itself.
3. **Pass per-string context in the brief.** The status script shows the English text but not what the string *is*, and contextless translation of short or ambiguous strings is how meaning inversions ship (a toggle label like "Open invitations" — which lets participants add other people — was once rendered as "invite-only"). The session that authored or reviewed the English strings has this context for free: for every new string, include a one-line note saying where it appears and what it does ("alert shown when a join tap races a deleted meeting", "button that opens the Patreon OAuth flow"). The translator agent can dig context out of `messages.py` and the views itself, but the brief note is cheaper and more reliable.
4. After all agents complete, run `uv run mb locales validate` again — must exit 0.

When English strings have been updated and translations need review, tell the translator agents to use `--review` mode. This compares English text against a git ref and shows what changed.

## CI enforcement

Two CI jobs enforce translation correctness:
- `validate-ids` — runs `uv run mb locales validate-ids`; ensures every message in code has a corresponding entry in `en.po`.
- `validate-locales` — runs `uv run mb locales validate`; ensures all non-English `.po` files are in sync with English. Depends on `build-translations`.
