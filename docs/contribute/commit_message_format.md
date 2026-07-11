---
icon: material/format-text
---

# Commit message format

Mitup rewrites your commit messages into a consistent, emoji-prefixed format so the history stays easy to scan.

## How it works

When you commit, the pre-commit hook automatically:

* Recognizes your commit type (case-insensitive: `feat`, `Feat`, or `FEAT` all work)
* Replaces the type prefix with an emoji from the configuration
* Capitalizes the first letter of your description
* Preserves scopes

### Example transformations

```
feat: add user authentication        → ✨ Add user authentication
fix(api): correct validation         → 🐛(api) Correct validation
docs: update installation guide      → 📚 Update installation guide
```

## Writing commit messages

### Basic format

Write your commits using the conventional commits format:

```
Type[(scope)]: description

[optional body]

[optional footer(s)]
```

### Rules

**Type** (required):

* Case-insensitive: `feat`, `Feat`, and `FEAT` all work
* Will be replaced with the corresponding emoji
* Must be one of the [allowed types](#allowed-commit-types)

**Scope** (optional):

* Enclosed in parentheses: `(api)`, `(handlers)`, `(cli)`
* Should be lowercase
* Can contain alphanumerics, hyphens, underscores, slashes, commas, and spaces

**Description** (required):

* Separated from type/scope with a colon and space: `: `
* Can be lowercase (will be auto-capitalized) or uppercase
* Describe what the commit does in imperative mood

**Body** (optional):

* Separated from subject by a blank line
* Can be multiple paragraphs
* Provide additional context about the changes

**Footer** (optional):

* Separated from body by a blank line
* Common footers: `BREAKING CHANGE:`, `Closes #123`, `Co-authored-by:`

### Valid examples

All these formats are valid:

```
feat: add user authentication
Feat: add user authentication
FEAT: add user authentication
fix(api): correct endpoint validation
docs(readme): update installation guide
test: add unit tests for api module
chore: update dependencies
```

### Invalid examples

These will fail with helpful error messages:

```
feat:missing space              # Missing space after colon
feat (scope): description       # Space between type and scope
build: compile project          # 'build' is not an allowed type
not a valid message             # No type prefix
```

## Allowed commit types

The allowed types are defined in [`commits_check_config.yaml`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/commits_check_config.yaml):

| Type | Emoji | Description |
|------|-------|-------------|
| **Feat** | ✨ | Introduce new features |
| **Fix** | 🐛 | Fix a bug |
| **Docs** | 📚 | Add or update documentation |
| **Refactor** | 🧹 | Refactor code without changing functionality |
| **Test** | 🧪 | Add or update tests |
| **Infra** | 🏗️ | Infrastructure changes |
| **CI** | 👷 | Continuous Integration changes |
| **Chore** | ⚙️ | Other changes that don't modify src or test files |
| **Revert** | ⏪ | Revert changes |
| **Merge** | 🔀 | Merge branches |
| **Update** | ⬆️ | Update dependencies or other changes |
| **Monitoring** | 📈 | Add or update monitoring |
| **WIP** | 🚧 | Work in progress |
| **Translations** | 🗣️ | Translations updates |

## Adding custom types

If you need a commit type that's not in the list, add it to `commits_check_config.yaml`:

```yaml
additional_commit_types:
  YourType:
    description: Your custom type description.
    emoji: 🎯
```

The formatter will automatically recognize and use your new type.

## Testing locally

### Test your commit message

Before committing, test how your message will be formatted:

<details>
<summary>Manual testing command</summary>

```bash
# Create a test commit message
echo "feat: add new feature" > /tmp/test_commit.txt

# Run the formatter
uv run mb ci check-commit /tmp/test_commit.txt

# View the result
cat /tmp/test_commit.txt
```

</details>

## Technical details

### Implementation

The commit message formatter is the `mb ci check-commit` command, implemented in [`tools/mb/src/mb/commit_check.py`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/tools/mb/src/mb/commit_check.py) and wired to the `commit-msg` hook in [`.pre-commit-config.yaml`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.pre-commit-config.yaml).

### How it's different

Unlike traditional validators that only check format, this tool:

* Transforms your messages instead of rejecting them
* Accepts any case for commit types
* Automatically capitalizes descriptions
* Uses emojis for visual clarity in git history

### Customizing validation

To change the validation rules, edit [`tools/mb/src/mb/commit_check.py`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/tools/mb/src/mb/commit_check.py). The regex that parses the commit format, the formatting logic, and the error messages all live in that file.

## Troubleshooting

### Hook not running

If the formatter isn't running automatically:

```bash
# Reinstall the commit-msg hook
pre-commit install --hook-type commit-msg
```

### Import errors

The formatter runs inside the workspace environment, which carries every dependency it needs. Always invoke it through uv so it resolves against that environment rather than your system Python:

```bash
uv run mb ci check-commit /tmp/test_commit.txt
```

### Commit rejected

If your commit is rejected:

1. Read the error message. It explains what's wrong and shows examples.
2. Check that your type is in the [allowed types list](#allowed-commit-types).
3. Ensure you have `: ` (colon + space) after the type/scope.
4. If you need a new type, add it to `commits_check_config.yaml`.

## Related documentation

* [Conventional Commits Specification](https://www.conventionalcommits.org/)
* [Making contributions](making_contributions.md)
* [Testing and validation](testing.md)
* [Setup development environment](setup.md)
