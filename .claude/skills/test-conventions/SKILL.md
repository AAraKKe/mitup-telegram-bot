---
name: test-conventions
description: >
  Testing conventions, helpers, and patterns for mitup_bot pytest tests. Auto-load when writing, reviewing, or
  modifying tests in the tests/ directory. Covers fixtures, MockApi, MockDbSession, metrics assertions,
  handler tests, conversation tests, failure modes, CLI tests, and DB integration tests.
---

# Test Conventions for mitup_bot

This skill provides reference material for writing tests. The agent definition covers rules and workflow;
this skill covers **how things work** — helper APIs, fixture chains, and patterns by test type.

## How to use this skill

Before writing a test, read the reference that matches what you're testing:

| Test type | Reference |
|---|---|
| Any test using fixtures, MockDbSession, or UpdateRequest | `references/fixtures.md` |
| Handler callback/command tests using MockApi | `references/api-and-assertions.md` |
| Asserting metrics via `MetricAssertions` fixture | `references/metrics.md` |
| Handler tests (callback, command, conversation flows) | `references/handler-tests.md` |
| Adding a handler to the centralized failure modes module | `references/failure-modes.md` |
| CLI command tests (recurrent events, notifications, etc.) | `references/cli-tests.md` |
| DB integration tests against real Postgres | `references/db-integration.md` |

Always also read the closest existing test file in the same directory — existing tests are the best exemplars for local patterns.
