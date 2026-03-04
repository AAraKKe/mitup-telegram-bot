---
name: type-checking
description: Type annotations, suppression rules, and how to run the type checker in this project.
user-invocable: false
---

# Type Checking

This project uses [ty](https://github.com/astral-sh/ty) as its type checker. The pinned version is in `pyproject.toml` under `[tool.hatch.envs.dev] dependencies`. Run it via:

```bash
hatch run dev:type-check
```

## Suppressing false positives

`ty` is pre-stable and does not yet support all Python typing features. Suppress genuine false positives with a `# ty: ignore[rule-name]` comment.

<critical_rules>
Every `# ty: ignore` comment must include a GitHub issue URL on the same line. No exceptions, no grandfathering. Enforced by `bin/check_ty_ignores.py` in CI.
</critical_rules>

```python
result = some_call()  # ty: ignore[missing-argument]  https://github.com/astral-sh/ty/issues/XXXX
```

The CI job `check-ty-ignores` queries the GitHub API and warns when a referenced issue is closed, signaling the suppression can be removed.

### Adding a new suppression

1. Confirm the error is a genuine false positive (not a real bug in your code).
2. Search the [ty issue tracker](https://github.com/astral-sh/ty/issues) for an existing report. Open a new issue if none exists.
3. Add the suppression with the rule name and issue URL on the same line.
4. Run `hatch run dev:type-check` to verify the suppression clears the error.

### Removing a suppression

1. Remove the `# ty: ignore` comment.
2. Run `hatch run dev:type-check`. If no error appears, the fix has landed — done.
3. If the error reappears, verify the installed `ty` matches the version in `pyproject.toml`. If not, run `hatch env prune && hatch env create dev` to refresh the environment.
4. If the version is correct and the error still appears, restore the comment — the fix has not fully landed yet.

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

### `Concatenate` / `ParamSpec` (ty#2759)

`ty` lacks support for `typing.Concatenate`, causing false positives on functions decorated with `with_session` or `with_async_session` from `mitup_bot.db`. These decorators use `Callable[Concatenate[Session, P], R]` to strip the leading `Session` parameter, but `ty` does not understand this and reports `missing-argument` at call sites and `invalid-return-type` on the decorator return statements.

Tracked in: https://github.com/astral-sh/ty/issues/2759

Affected patterns:

```python
# Decorator definitions in mitup_bot/db.py — ty: ignore[invalid-return-type]
def with_session[**P, R](func: Callable[Concatenate[Session, P], R]) -> Callable[P, R]:
    ...

# Call sites — ty: ignore[missing-argument]
@with_async_session
async def my_handler(session: Session, update: Update, context: MitupContext) -> int:
    ...

await my_handler(update, context)  # ty: ignore[missing-argument]  https://github.com/astral-sh/ty/issues/2759
```

## CI enforcement

The `check-ty-ignores` job (defined in `.gitlab/ci/test.yml`) runs `bin/check_ty_ignores.py` on merge requests. It scans `mitup_bot/`, `tests/`, and `bin/` and:

- Fails if any suppression is missing a GitHub issue URL (see [Suppressing false positives](#suppressing-false-positives)).
- Queries the GitHub API and warns when a referenced issue is closed.

The job runs with `allow_failure: true`, so it surfaces as a warning without blocking the merge request.
