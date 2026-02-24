---
name: ty-ignore
description: Add a proper ty type-checker suppression comment with the required GitHub issue URL.
user-invocable: true
argument-hint: "[rule-name] [optional: file:line]"
allowed-tools: Read, Edit, WebSearch
---

Format required:
```python
result = call()  # ty: ignore[rule-name]  https://github.com/astral-sh/ty/issues/XXXX
```

Steps:
1. Identify the ty rule name from the error (e.g., `missing-argument`, `unresolved-import`).
2. Search https://github.com/astral-sh/ty/issues for the relevant open issue.
3. Insert the comment on the flagged line in the correct format.
4. Warn if the referenced issue is already closed (would fail the `check-ty-ignores` CI job).

Known false positive: `Concatenate`/`ParamSpec` → ty#2759 → applies to `@with_session` / `@with_async_session` decorators.
