---
icon: material/format-text
---

# :pen: Commit Message Format

Mitup uses a custom commit message formatter that automatically transforms your commit messages into a consistent, emoji-based format. This makes the commit history more readable and visually informative.

## How It Works

When you commit, the pre-commit hook automatically:

* **Recognizes** your commit type (case-insensitive: `feat`, `Feat`, or `FEAT` all work)
* **Replaces** the type prefix with an emoji from the configuration
* **Capitalizes** the first letter of your description
* **Preserves** scopes and breaking change indicators

### Example Transformations

Here's what happens to your commits:

```
feat: add user authentication        → ✨ Add user authentication
fix(api): correct validation         → 🐛(api) Correct validation
docs: update installation guide      → 📚 Update installation guide
refactor(handlers)!: change callback → 🧹(handlers)! Change callback
```

## :memo: Writing Commit Messages

### Basic Format

Write your commits using the conventional commits format:

```
Type[(scope)][!]: description

[optional body]

[optional footer(s)]
```

### Rules

**Type** (required):

* Case-insensitive (use `feat`, `Feat`, or `FEAT` - all work!)
* Will be replaced with the corresponding emoji
* Must be one of the [allowed types](#allowed-commit-types)

**Scope** (optional):

* Enclosed in parentheses: `(api)`, `(handlers)`, `(cli)`
* Should be lowercase
* Can contain alphanumerics, hyphens, underscores, slashes, commas, and spaces

**Breaking Change Indicator** (optional):

* Add `!` after type or scope to indicate breaking changes
* Example: `feat!:` or `feat(api)!:`

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

### Valid Examples

All these formats are valid:

```
✅ feat: add user authentication
✅ Feat: add user authentication
✅ FEAT: add user authentication
✅ fix(api): correct endpoint validation
✅ docs(readme): update installation guide
✅ refactor(handlers)!: change callback structure
✅ test: add unit tests for api module
✅ chore: update dependencies
```

### Invalid Examples

These will fail with helpful error messages:

```
❌ feat:missing space              # Missing space after colon
❌ feat (scope): description       # Space between type and scope
❌ build: compile project          # 'build' is not an allowed type
❌ not a valid message             # No type prefix
```

## :sparkles: Allowed Commit Types

The allowed types are defined in [`commits_check_config.yaml`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/commits_check_config.yaml):

| Type | Emoji | Description |
|------|-------|-------------|
| **Feat** | ✨ | Introduce new features |
| **Fix** | 🐛 | Fix a bug |
| **Docs** | 📚 | Add or update documentation |
| **Style** | 💎 | Improve code style without affecting functionality |
| **Refactor** | 🧹 | Refactor code without changing functionality |
| **Perf** | 🚀 | Improve performance |
| **Test** | 🧪 | Add or update tests |
| **Infra** | 🏗️ | Infrastructure changes |
| **CI** | 👷 | Continuous Integration changes |
| **Chore** | ♻️ | Other changes that don't modify src or test files |
| **Revert** | ⏪ | Revert changes |
| **Merge** | 🔀 | Merge branches |
| **Update** | 🚀 | Update dependencies or other changes |
| **Monitoring** | 📈 | Add or update monitoring |
| **WIP** | 🚧 | Work in progress |
| **Translations** | 🗣️ | Translations updates |

## :wrench: Adding Custom Types

If you need a commit type that's not in the list, you can add it to `commits_check_config.yaml`:

```yaml
additional_commit_types:
  YourType:
    description: Your custom type description.
    emoji: 🎯
```

The formatter will automatically recognize and use your new type.

## :test_tube: Testing Locally

### Test Your Commit Message

Before committing, you can test how your message will be formatted:

<details>
<summary>Manual testing command</summary>

```bash
# Create a test commit message
echo "feat: add new feature" > /tmp/test_commit.txt

# Run the formatter
./bin/check_commit_message.py /tmp/test_commit.txt

# View the result
cat /tmp/test_commit.txt
```

</details>

### Run the Test Suite

The repository includes a comprehensive test suite:

```bash
python bin/test_commit_checker.py
```

This runs 18 test cases covering various scenarios including:

* Case-insensitive type matching
* Scope handling
* Breaking change indicators
* Description capitalization
* Invalid format detection

## :book: Technical Details

### Implementation

The commit message formatter is implemented in [`bin/check_commit_message.py`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/bin/check_commit_message.py) and integrated with pre-commit hooks via [`.pre-commit-config.yaml`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.pre-commit-config.yaml).

### How It's Different

Unlike traditional validators that only check format, this tool:

* **Transforms** your messages instead of rejecting them
* **Accepts** any case for commit types
* **Automatically capitalizes** descriptions
* **Uses emojis** for visual clarity in git history

### Customizing Validation

If you need to modify the validation rules, edit `bin/check_commit_message.py`:

<details>
<summary>Key customization points</summary>

* **Line 20-27**: Regex pattern for commit format
* **Line 57-118**: Formatting logic
* **Line 120-177**: Error message formatting

For example, to change how descriptions are capitalized, modify the logic around line 95-97.

</details>

## :question: Troubleshooting

### Hook Not Running

If the formatter isn't running automatically:

```bash
# Reinstall the commit-msg hook
pre-commit install --hook-type commit-msg
```

### Import Errors

The formatter requires PyYAML. If you get import errors:

```bash
# Install PyYAML in your environment
pip install pyyaml
```

Or if using hatch:

```bash
hatch run dev:pip install pyyaml
```

### Commit Rejected

If your commit is rejected:

1. Read the error message - it explains what's wrong and shows examples
2. Check that your type is in the [allowed types list](#allowed-commit-types)
3. Ensure you have `: ` (colon + space) after the type/scope
4. If you need a new type, add it to `commits_check_config.yaml`

## :link: Related Documentation

* [Conventional Commits Specification](https://www.conventionalcommits.org/)
* [Making Contributions](committing_to_repo.md)
* [Local Validation](local_validation.md)
* [Setup Development Environment](code_contributor.md)

---

*The commit message formatter helps maintain a clean, consistent git history that's easy to read and understand at a glance.*
