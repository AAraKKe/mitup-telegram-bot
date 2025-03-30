# Committing to the Repository

Thank you for considering contributing to Mitup! We appreciate all contributions, from bug reports and documentation improvements to code changes. Please review our [Code of Conduct](CODE_OF_CONDUCT.md) before participating.

While this page focuses on the process for submitting code changes, you can find other ways to help in the [Being a Supporter guide](../collaborate/supporter.md).

To contribute changes to this project, please follow these steps:

## 1. Create an Issue

Before starting development, open an issue to track the proposed change. This ensures transparency and allows for discussion. Use the appropriate template for the type of issue:

*   **Bug Report:** Report unexpected behavior or errors. [Create Bug Report](https://gitlab.com/meetupbot/mitup-telegram-bot/-/issues/new?issuable_template=Bug)
*   **Feature Proposal:** Suggest a new feature or enhancement. [Propose Feature](https://gitlab.com/meetupbot/mitup-telegram-bot/-/issues/new?issuable_template=Feature%20Proposal)
*   **Documentation Improvement:** Suggest changes to the documentation. [Improve Documentation](https://gitlab.com/meetupbot/mitup-telegram-bot/-/issues/new?issuable_template=Improve%20Documentation)
*   **Task:** For general tasks not covered by other templates. [Create Task](https://gitlab.com/meetupbot/mitup-telegram-bot/-/issues/new?issuable_template=Task)
*   **Translation:** Contribute or update translations. [Add Translation](https://gitlab.com/meetupbot/mitup-telegram-bot/-/issues/new?issuable_template=Translation)
*   **New Language Request:** Propose adding support for a new language. [Request Language](https://gitlab.com/meetupbot/mitup-telegram-bot/-/issues/new?issuable_template=New%20Language%20Request)

Discuss the issue with maintainers if necessary before proceeding.

## 2. Fork and Clone the Repository

Fork the repository to your own namespace and clone it locally to begin development.

## 3. Develop Your Changes

Create a new branch for your changes. Name the branch starting with the issue number followed by a descriptive name (e.g., `123-fix-validation-logic`). This helps automatically link the branch and subsequent merge request to the issue in GitLab.

Before committing your changes, ensure you have completed the [Setup steps](../collaborate/code-contributor.md) and that `pre-commit` hooks are installed and running correctly. This performs local validation checks (like linting and formatting) automatically when you commit, simplifying the review process. We also recommend running the full [Local Validation checks](local-validation.md) before opening a Merge Request.

Implement the required changes in your local repository. Commit your work with clear, concise messages following the [Conventional Commits standard](https://www.conventionalcommits.org/en/v1.0.0/). This standard is enforced by a `pre-commit` hook defined in the [`.pre-commit-config.yaml`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.pre-commit-config.yaml) file. The specific types supported in this repository are configured in the [`commits_check_config.yaml`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/commits_check_config.yaml) file.

## 4. Merge Request Process

Once development is complete, push your changes to your fork and open a Merge Request (MR) against the main repository's `main` branch.

*   **Description:** Provide a clear description of the changes, linking the related issue.
*   **Pipeline Validation:** The CI/CD pipeline will automatically run tests and linters against your MR. Ensure all pipeline jobs pass. Address any reported failures.
*   **Sourcery AI Review:** We utilize [Sourcery AI](https://sourcery.ai/) for automated code review. Address any relevant suggestions provided by Sourcery's analysis.
*   **Human Review:** A project maintainer will review the MR after automated checks are satisfactory. Address any feedback provided during the review.
*   **Approval and Merge:** Once approved, the maintainer will merge your MR into the main codebase.

Thank you for your contribution.

---
*By contributing, you agree that your contributions will be licensed under the [MIT License](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/LICENSE).*
