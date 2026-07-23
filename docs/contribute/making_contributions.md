---
icon: material/source-commit
---

# Contribution workflow

Please review our [code of conduct](../collaborate/code_of_conduct.md) before taking part. This page is about code; other ways to help are on the [ways to support Mitup page](../collaborate/donation.md).

## Start from an accepted issue

Every merge request starts from an issue a maintainer has accepted. This keeps work aligned before anyone writes code, and it gives your change a home in a milestone.

An issue is ready to work on when it carries one of `state::ready`, `state::accepted`, or `state::help-wanted`. An issue still tagged `state::discussion` is being talked through, so wait for a maintainer to move it before you start. Merge requests that arrive without an accepted issue are closed with a pointer back to this rule.

There is one exception: a typo-level fix to the docs or a translation can go straight to a merge request.

To raise something new, open an issue from the [template chooser](https://gitlab.com/meetupbot/mitup-telegram-bot/-/issues/new/choose) and pick the template that matches. Then wait for a maintainer to mark it ready.

## Fork and clone

External contributors work from a fork. Fork the repository into your own namespace on GitLab, then clone the fork and push your branches there:

```bash
git clone git@gitlab.com:<your-namespace>/mitup-telegram-bot.git
cd mitup-telegram-bot
```

The rest of [setup](setup.md) is the same from there.

## Branch

Name the branch with the issue number first, then a short description: `123-fix-validation-logic`. The leading number lets GitLab auto-link the branch and its merge request to the issue.

## Develop

Read the skill for the area you are touching before you change it. The [building with AI](working_with_agents.md) page explains where the skills live and how to point an assistant at them.

Run targeted tests as you go, then the full gate before you open the merge request:

```bash
uv run mb validate
```

[Testing and validation](testing.md) covers both loops.

## Commit

Write commits in conventional format, `Type[(scope)]: description`. The [commit format](commit_message_format.md) page has the details.

## Open the merge request

Push to your fork and open a merge request against `main`.

* Write a clear description and link the issue with `Closes #N`.
* Make sure the pipeline passes; it runs the same checks as `mb validate`.
* [Sourcery AI](https://sourcery.ai/) reviews the merge request automatically; address anything relevant it raises.
* A maintainer reviews after that, and merges once it is ready.

---

By contributing, you agree that your contributions will be licensed under the [MIT License](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/LICENSE).
