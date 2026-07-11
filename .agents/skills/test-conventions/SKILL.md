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

## Test signatures must be fully typed

Every parameter in a test or fixture signature must have a type annotation, including parameters that pytest injects from fixtures. A signature like `async def test_foo(web_app, ptb_app: MagicMock):` is **incomplete** — annotate every parameter (`async def test_foo(web_app: FastAPI, ptb_app: MagicMock):`). The annotation requirement from the `coding-standards` skill applies to test parameters with no exception.

## Cover new and changed code before handing off

CI measures coverage (the `test-suite` and `test-db` jobs in `.gitlab/ci/test.yml` run pytest with coverage; `coverage-report` combines them into the tracked total), but a healthy *total* can still hide an undertested new module. Before declaring a testing task done or opening an MR, check coverage of the code this branch adds or changes — don't rely on the aggregate.

Run the same command CI runs; it prints a per-file `term-missing` table:

```bash
uv run mb test --cov
```

For **every module you added or touched**, read its line. Then either:

- bring it up to the project's coverage baseline (the level CI enforces on the total — see `.gitlab/ci/test.yml`, don't assume a fixed number), **or**
- justify the shortfall explicitly in the MR description. The common legitimate reason here: logic exercised only by db-gated integration tests (`pytest.mark.db_test`), which run in the separate `test-db` job and do **not** feed the unit coverage total — add mock-session unit tests to close the gap, or call out that the real coverage lives in the db suite.

A new feature module landing well below the baseline with no justification is incomplete. To read just the modules you care about, filter the `term-missing` output (e.g. `uv run mb test --cov 2>&1 | grep mitup_bot/<package>`).
