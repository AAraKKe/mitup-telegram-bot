---
icon: material/format-text
---

# Commit message format

Mitup rewrites your commit messages into a consistent, emoji-prefixed format so the history stays easy to scan.

## What the hook does

When you commit, the pre-commit hook recognizes your commit type, replaces the type prefix with the matching emoji, capitalizes the first letter of the description, and preserves any scope:

```
feat: add user authentication        → ✨ Add user authentication
fix(api): correct validation         → 🐛(api) Correct validation
docs: update installation guide      → 📚 Update installation guide
```

## Writing commit messages

Write your commits in conventional format:

```
Type[(scope)]: description
```

* **Type** is case-insensitive (`feat`, `Feat`, and `FEAT` all work) and must be a known type. The types and their emoji are defined in [`commits_check_config.yaml`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/commits_check_config.yaml); common ones are `Feat`, `Fix`, `Docs`, `Refactor`, and `Test`.
* **Scope** is optional, lowercase, and wrapped in parentheses: `(api)`, `(handlers)`.
* **Description** follows a colon and a space, and reads in the imperative mood.

### Valid

```
feat: add user authentication
fix(api): correct endpoint validation
docs(readme): update installation guide
test: add unit tests for the api module
```

### Invalid

```
feat:missing space              # Missing space after the colon
feat (scope): description       # Space between type and scope
build: compile project          # 'build' is not a known type
not a valid message             # No type prefix
```

## Troubleshooting

**The hook isn't running.** Reinstall the `commit-msg` hook:

```bash
pre-commit install --hook-type commit-msg
```

**The commit was rejected.** Read the error message; it says what is wrong and shows examples. Check that your type is one of the known types and that a colon and a space follow the type or scope.
