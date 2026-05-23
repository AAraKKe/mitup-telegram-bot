---
icon: material/check-circle-outline
---

# Local validation

Before submitting a Merge Request, it's crucial to run the project's validation checks locally. This ensures your changes meet the required code quality standards and helps catch issues before they reach the CI pipeline, speeding up the review process.

We use [Hatch](https://hatch.pypa.io/latest/) to manage project environments and run tasks. After completing the [Setup steps](setup.md), you can use the following Hatch commands. Ensure you are in the `dev` environment (enter it using `hatch shell dev`) before running them:

## Formatting

*   `hatch run dev:format`: Automatically formats the code using Ruff according to the project's style guide.
*   `hatch run dev:format-check`: Checks if the code is correctly formatted without making changes. This is useful for verifying formatting in CI or pre-commit hooks.

## Linting

*   `hatch run dev:lint`: Runs the Ruff linter to check for potential errors, bugs, and style issues. It shows differences but doesn't modify files.
You can pass arguments to `ruff check` via hatch for more targeted linting (e.g., `hatch run dev:lint -- <ruff_args>`).
*   `hatch run dev:fix-lint`: Runs the Ruff linter and automatically attempts to fix any detected issues.

## Type checking

*   `hatch run dev:type-check`: Performs static type checking using Pyright to catch type errors in the codebase.

## Testing

*   `hatch run dev:test`: Runs the full test suite using Pytest with coverage reporting. This executes tests in parallel (`-n 4`).
You can pass arguments directly to `pytest` via hatch (e.g., `hatch run dev:test -- -k test_specific_feature`).
*   `hatch run dev:test-cov`: Runs the full test suite and generates detailed coverage reports (XML, JUnit XML, JSON) typically used in CI.

## Combined validation

*   `hatch run dev:validate`: Runs a sequence of checks: `format-check`, `lint`, `type-check`, and `test`. This is a comprehensive command to ensure that your changes pass all major local validation steps.

Run `hatch run dev:validate` before pushing your changes and opening a merge request.
