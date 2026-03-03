---
name: type-checking
description: Skills that contians knowledge about how do type annotations, validate the proper use of them and run type checker in this project.
user-invocable: false
---

# Type Checking

This project uses [ty](https://github.com/astral-sh/ty) as its type checker. The pinned version is in `pyproject.toml` under `[tool.hatch.envs.dev] dependencies`. Run it via:

```bash
hatch run dev:type-check
```

## Suppressing false positives

`ty` is pre-stable and does not yet support all Python typing features. When a false positive is encountered, suppress it with a `# ty: ignore[rule-name]` comment.

**Every `ty: ignore` comment must include a GitHub issue URL** linking to the upstream bug that causes the false positive. This is enforced by `bin/check_ty_ignores.py` in CI.

Correct format:

```python
result = some_call()  # ty: ignore[missing-argument]  https://github.com/astral-sh/ty/issues/XXXX
```

The issue URL allows automated tracking: the CI job `check-ty-ignores` queries the GitHub API and warns when a referenced issue is closed, signaling that the suppression can likely be removed.

### Adding a new suppression

1. Confirm the error is a genuine false positive (not a real bug in your code).
2. Search the [ty issue tracker](https://github.com/astral-sh/ty/issues) for an existing report. Open a new issue if none exists.
3. Add the suppression with the rule name and issue URL on the same line.
4. Run `hatch run dev:type-check` to verify the suppression clears the error.

### Removing a suppression

1. Remove the `# ty: ignore` comment.
2. Run `hatch run dev:type-check` — if no error appears, the upstream fix has landed.
3. If the error reappears, first validate that the version of `ty` is the one specified in the `pyproject.toml`, sometimes the `hatch` environment has not been updated locally.
4. If the version is not the same, update the environment by running `hatch env prune && hatch env create dev`.
5. If it is, restore the comment (the issue may not be fully fixed in the installed `ty` version).

## Prefer `cast` over `# type: ignore`

When you have already narrowed a type through control flow (e.g., filtered `None` values in a loop or conditional), use `typing.cast` instead of `# type: ignore[arg-type]`. This communicates intent and avoids masking real errors:

```python
# Bad — suppresses all arg-type errors on this line
future.sort(key=lambda m: m.datetime)  # type: ignore[arg-type]

# Good — documents that datetime is known non-None here
future.sort(key=lambda m: cast(dt.datetime, m.datetime))
```

Reserve `# type: ignore` (and `# ty: ignore`) for genuine false positives in the type checker, not for type narrowing that the checker cannot infer.

## Known false positives

This section documents `ty` bugs that currently require `# ty: ignore` suppressions in the codebase.

**Maintenance rule:** When removing suppressions because an upstream issue has been fixed, also remove the corresponding subsection below. Do not leave entries for issues that are no longer active — stale entries mislead agents into thinking a workaround is still needed. The `check-ty-ignores` CI job will flag when an issue is closed; use that signal to clean up both the code suppressions and this section.

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

The `check-ty-ignores` job (defined in `.gitlab/ci/test.yml`) runs `bin/check_ty_ignores.py` on merge requests. It:

- Scans `mitup_bot/`, `tests/`, and `bin/` for all `# ty: ignore` comments.
- Fails if any suppression is **missing a GitHub issue URL** (untracked).
- Queries the GitHub API to check whether referenced issues have been **closed**.
- Reports actionable items when suppressions can be removed.

The job is configured with `allow_failure: true`, so it surfaces as a **warning** on the merge request without blocking it.
