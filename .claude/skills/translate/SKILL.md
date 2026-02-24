---
name: translate
description: Add a new user-facing string to the project's translation catalog.
user-invocable: true
argument-hint: "[English source text]"
allowed-tools: Read, Edit, Bash
---

Read the `translations` reference skill for full conventions before starting.

Steps:
1. Ask which message class the string belongs to: `Messages`, `ButtonMessages`, `MeetingMessages`, `SettingsMessages`, or `NotificationMessages` — all in `mitup_bot/utils/messages.py`.
2. Add the new `StrEnum` member (value = English source text = msgid).
3. Run: `hatch run dev:update-source-language`
4. Run: `hatch run dev:update-locales` (this adds empty `msgstr` entries to all locale `.po` files)
5. Ask the user: "Shall I use the `translator` subagent to translate this string into all supported languages?"
   - If yes: delegate to the `translator` subagent with the new msgid as context.
   - If no: remind the user to fill in each `.po` file manually.
6. Run: `hatch run dev:build-locales`
