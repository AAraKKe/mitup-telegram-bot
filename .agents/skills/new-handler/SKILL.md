---
name: new-handler
description: Scaffold a new Telegram bot handler package following project conventions.
user-invocable: true
argument-hint: "[feature-name] [command|callback|conversation|inline]"
allowed-tools: Read, Write, Bash, Glob
---

Read `mitup_bot/handlers/CLAUDE.md` for the full conventions before starting.

Ask the user for:
- Feature name (e.g., `reminders`, `settings`)
- Handler type: `command`, `callback`, `conversation`, or `inline`
- Related models (if any)

Then scaffold:
1. Create `mitup_bot/handlers/<feature>/` with:
   - `__init__.py` (empty)
   - `enums.py` with a `HandlerId` subclass for this feature
   - `entry.py` with the entry-point handler function(s), `@with_session`, and guards
2. Register the handler in `mitup_bot/app.py` using the correct `register_*` method.
3. Create `tests/handlers/test_<feature>.py` mirroring the test structure.
4. Remind the user to add the handler context to `CONTEXTS` in `tests/test_failure_modes.py`.
