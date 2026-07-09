---
icon: material/flask-outline
---

# Testing and validation

Mitup runs its checks through [Hatch](https://hatch.pypa.io/latest/). Every script below is defined under `[tool.hatch.envs.dev.scripts]` in [`pyproject.toml`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/pyproject.toml). Finish the [setup steps](setup.md) first, then run these from the repo root.

## Two ways to run tests

There are two entry points, and they answer different questions.

* `hatch run dev:test-hook <paths or pytest args>` runs the tests you point it at, in parallel, with no coverage and short tracebacks. It is the fast loop you use while writing code. Pass it a file, a directory, or a `-k` expression: `hatch run dev:test-hook tests/handlers -k reminder`.
* `hatch run dev:validate` is the full gate. It runs `format-check`, `lint`, `type-check`, and the whole test suite with coverage, in that order. Run it before you push and before you open a merge request. CI runs the same checks and rejects merge requests that fail them.

The steps inside `validate` keep going even when one fails, so a single run reports every problem at once instead of stopping at the first.

## What each check does

* `hatch run dev:format` rewrites your code with Ruff's formatter. `hatch run dev:format-check` reports formatting drift without touching files.
* `hatch run dev:lint` runs the Ruff linter and shows a diff of what it would change. `hatch run dev:fix-lint` applies the safe fixes. `hatch run dev:fix` does both format and lint fixes in one go.
* `hatch run dev:type-check` runs [ty](https://github.com/astral-sh/ty), the project's type checker.
* `hatch run dev:test` runs the full suite with coverage. `hatch run dev:test-hook` is the same suite without coverage, for speed.

## Handler tests use a mock Telegram

Handler and command tests never talk to Telegram. They drive a handler with a fake `UpdateRequest` and assert against `MockApi`, a stand-in for the real Telegram client that records every call. Instead of checking raw call arguments, you assert intent with typed helpers like `context.api.assert_send_message_called(update, view)` or `context.api.assert_answer_callback_query_called(update)`, which print a readable diff when the call does not match.

## Common guard failures live in one place

Most handlers share the same failure paths: the user is not found, the meeting does not exist, the caller does not own the meeting, the callback data is malformed. Those are not re-tested in every handler file. Instead a handler is registered in the failure-modes matrix at [`tests/handlers/test_failure_modes.py`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/tests/handlers/test_failure_modes.py), which generates a parametrized test for each error mode you declare. If your handler uses guards, register it there rather than copying the cases by hand.

## Database integration tests

Most tests run against a mock session. The tests under [`tests/models/db_behavior/`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/tests/models/db_behavior/) run against a real Postgres instance that `testcontainers` spins up for the run, so they need Docker running locally.

```bash
hatch run dev:test-db
hatch run dev:test-db -- -k "cascade_delete" -v
```

These tests are skipped during a normal `hatch run dev:test` run and only execute under the `--db-tests` flag that `test-db` passes for you.

## The suite runs once per language in CI

Mitup ships in several languages, and CI runs the full test suite once for each one: `en`, `es_ES`, `gl_ES`, `de_DE`, `pt_BR`, and `it_IT`. A test that passes locally under English can still fail in CI if it hard-codes an English string, so assert against message constants rather than literal text. Locally you can reproduce a single language with `hatch run dev:test -- tests --lang gl_ES`.

## Going deeper

The conventions for fixtures, `MockApi`, metrics assertions, conversation flows, and the failure-modes matrix live in the [`test-conventions` skill](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.claude/skills/test-conventions/SKILL.md). Read the reference that matches what you are testing, then read the closest existing test in the same directory before writing your own.
