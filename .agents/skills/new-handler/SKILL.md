---
name: new-handler
description: Scaffold a new Telegram bot handler package following project conventions.
user-invocable: true
argument-hint: "[feature-name] [command|callback|conversation|inline]"
allowed-tools: Read, Write, Bash, Glob
---

Load the `handler-conventions` skill (`.agents/skills/handler-conventions/SKILL.md`) for the full conventions before starting.

Ask the user for:
- Feature name (e.g., `reminders`, `settings`)
- Handler type: `command`, `callback`, `conversation`, or `inline`
- Related models (if any)

Then scaffold:
1. Create `apps/bot/mitup_bot/handlers/<feature>/` with:
   - `__init__.py` (empty)
   - `enums.py` with a `HandlerId` subclass for this feature
   - `entry.py` with the entry-point handler function(s), `@with_session`, and guards
2. Decorate the entry handler with the correct `@HandlersRegistry.register_*` method, then import the new package in `apps/bot/mitup_bot/handlers/__init__.py`.
3. Create test file(s) under `tests/bot/handlers/<feature>/test_<module>.py` mirroring the test structure.
4. Remind the user to add the handler context to `CONTEXTS` in `tests/bot/handlers/test_failure_modes.py`.
