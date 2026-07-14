---
name: type-checking
description: >-
  Everything about the `ty` type checker in this project — how to run it, type annotation conventions, when to prefer `cast` over `# type: ignore`, documented known-false-positives, the `check-ty-ignores` CI job, and the exact format required for `# ty: ignore[rule-name]` suppressions (including the mandatory GitHub issue URL). Use this skill whenever a `ty` error needs investigation, whenever a suppression is added/removed/reviewed, or whenever someone asks "how do I silence this type error" — even if the request is phrased as a quick task like "add a ty-ignore here". Also covers the interactive workflow for inserting a suppression on a specific file/line.
user-invocable: true
argument-hint: "[rule-name] [optional: file:line]"
allowed-tools: Read, Edit, WebSearch, Bash, Grep
---

# Type Checking

This project uses [ty](https://github.com/astral-sh/ty) as its type checker. The pinned version is in `pyproject.toml` under `[dependency-groups]`. Run it via:

```bash
uv run mb typecheck
```

## Suppressing false positives

`ty` is pre-stable and does not yet support all Python typing features. Suppress genuine false positives with a `# ty: ignore[rule-name]` comment.

<critical_rules>
Every `# ty: ignore` comment must include a GitHub issue URL on the same line. No exceptions, no grandfathering. Enforced by `uv run mb ci check-ty-ignores` in CI.
</critical_rules>

```python
result = some_call()  # ty: ignore[missing-argument]  https://github.com/astral-sh/ty/issues/XXXX
```

The CI job `check-ty-ignores` queries the GitHub API and warns when a referenced issue is closed, signaling the suppression can be removed.

### Adding a new suppression

1. Confirm the error is a genuine false positive (not a real bug in your code).
2. Search the [ty issue tracker](https://github.com/astral-sh/ty/issues) for an existing report. Open a new issue if none exists.
3. Add the suppression with the rule name and issue URL on the same line.
4. Run `uv run mb typecheck` to verify the suppression clears the error.

### Removing a suppression

1. Remove the `# ty: ignore` comment.
2. Run `uv run mb typecheck`. If no error appears, the fix has landed — done.
3. If the error reappears, verify the installed `ty` matches the version in `pyproject.toml`. If not, run `uv sync` to refresh the environment.
4. If the version is correct and the error still appears, restore the comment — the fix has not fully landed yet.

## Interactive suppression workflow

Invoke this skill directly (via `/type-checking <rule-name> [file:line]`) when you want it to drive the suppression insertion for you. The workflow is the same rules above, run as an interactive task:

1. Identify the `ty` rule name from the error message (e.g. `missing-argument`, `unresolved-import`, `invalid-return-type`). If the caller passed it as an argument, use that.
2. Search <https://github.com/astral-sh/ty/issues> for an open issue covering the false positive. If a match exists, use its URL. If none exists, pause and tell the caller to open one before proceeding — the suppression cannot land without an issue link.
3. Insert the comment on the flagged line in the exact format shown above (rule name in brackets, two spaces, full issue URL). If the caller passed `file:line`, go straight there; otherwise ask which location to modify.
4. Warn the caller if the referenced issue is already **closed** — the `check-ty-ignores` CI job will flag the suppression as stale, and the fix may already be available in a newer `ty` version.
5. Run `uv run mb typecheck` to confirm the error clears.

Before picking a fresh URL, check the "Known false positives" section below — many common suppressions already have a documented issue you can reuse.

## Prefer `cast` over `# type: ignore`

When the type has already been narrowed through control flow (e.g., `None` filtered out in a loop), use `typing.cast` instead of `# type: ignore[arg-type]`. This documents intent without masking other errors on the same line.

```python
# Bad — suppresses all arg-type errors on this line
future.sort(key=lambda m: m.datetime)  # type: ignore[arg-type]

# Good — documents that datetime is known non-None here
future.sort(key=lambda m: cast(dt.datetime, m.datetime))
```

## Known false positives

This section documents active `ty` bugs that require suppressions in the codebase. When a suppression is removed (because the upstream issue closed), remove the corresponding subsection here. The `check-ty-ignores` CI job signals when an issue has closed.

## CI enforcement

The `check-ty-ignores` job (defined in `.gitlab/ci/test.yml`) runs `uv run mb ci check-ty-ignores` on merge requests. It scans `tests/` and `tools/` and:

- Fails if any suppression is missing a GitHub issue URL (see [Suppressing false positives](#suppressing-false-positives)).
- Queries the GitHub API and warns when a referenced issue is closed.

The job runs with `allow_failure: true`, so it surfaces as a warning without blocking the merge request.
