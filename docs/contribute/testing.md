---
icon: material/flask-outline
---

# Testing and validation

Every check runs through the `mb` developer CLI. Finish the [setup steps](setup.md) first, then run these from the repo root. See [the mb CLI](dev_cli.md) for the full command surface.

## Two ways to run tests

There are two entry points, and they answer different questions.

* `uv run mb test [paths or pytest args]` runs the tests you point it at, fast, with no coverage. It's the loop you use while writing code. Pass it a file, a directory, or a `-k` expression, and anything else rides through to pytest: `uv run mb test tests/handlers -k broadcast`. With no path it runs the whole suite.
* `uv run mb validate` is the full gate. It runs the formatter check, the linter, the type checker, and the whole test suite with coverage, and keeps going even when one step fails, so a single run reports every problem at once instead of stopping at the first. Run it before you push and before you open a merge request. CI runs the same checks and rejects merge requests that fail them.

`mb validate --all` extends the gate with the database suite, the locale-catalog validation, and the migration-graph check. That's the fullest local approximation of CI.

## What each check does

* `uv run mb format` rewrites your code with Ruff's formatter.
* `uv run mb lint` runs the Ruff linter.
* `uv run mb typecheck` runs [ty](https://github.com/astral-sh/ty), the project's type checker, over the code and the `mb` tool itself.
* `uv run mb fix` formats and applies every safe and unsafe lint fix in one pass, so review the resulting diff.
* `uv run mb test` runs the suite. Add `--cov` for a coverage report; leave it off for speed.

## Handler tests use a mock Telegram

Handler and command tests never talk to Telegram. They drive a handler with a fake `UpdateRequest` and assert against `MockApi`, a stand-in for the real Telegram client that records every call. Instead of checking raw call arguments, you assert intent with typed helpers like `context.api.assert_send_message_called(update, view)` or `context.api.assert_answer_callback_query_called(update)`, which print a readable diff when the call does not match.

## Common guard failures live in one place

Most handlers share the same failure paths: the user is not found, the meeting does not exist, the caller does not own the meeting, the callback data is malformed. Those are not re-tested in every handler file. Instead a handler is registered in the failure-modes matrix at [`tests/handlers/test_failure_modes.py`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/tests/handlers/test_failure_modes.py), which generates a parametrized test for each error mode you declare. If your handler uses guards, register it there rather than copying the cases by hand.

## Database integration tests

Most tests run against a mock session. The tests under [`tests/data/db_behavior/`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/tests/data/db_behavior) run against a real Postgres instance that `testcontainers` spins up for the run, so they need Docker running locally. They're skipped during a normal `mb test` run and only execute under the `--db` flag:

```bash
uv run mb test --db
uv run mb test --db tests/data/db_behavior -k cascades -v
```

## The suite runs once per language in CI

Mitup ships in several languages, and CI runs the full test suite once for each supported one. A test that passes locally under English can still fail in CI if it hard-codes an English string, so assert against message constants rather than literal text. Reproduce a single language locally with the `--lang` flag:

```bash
uv run mb test --lang gl_ES
```

The set of languages is [`SUPPORTED_LANGUAGES`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/libs/core/mitup_bot/translations.py) in code, and CI derives its matrix from it. `uv run mb ci check-languages` fails the pipeline if the two ever drift apart, so the matrix can't silently fall behind a newly added language.

## Going deeper

The conventions for fixtures, `MockApi`, metrics assertions, conversation flows, and the failure-modes matrix live in the [`test-conventions` skill](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/test-conventions/SKILL.md). Read the reference that matches what you are testing, then read the closest existing test in the same directory before writing your own.
