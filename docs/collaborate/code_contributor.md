---
description: "How to contribute code to Mitup: the bot is MIT-licensed Python and Postgres, developed on GitLab, and work starts from an accepted issue."
icon: material/code-tags
---

# Contribute code

Mitup started in 2015 as a side project to solve one problem: organizing meetings in a busy Telegram group. As the bot grew and more people came to rely on it, the same question kept landing in the inbox:

> _"Can I help build this?"_

So Mitup is now open source. The codebase is public, and contributions are welcome from anyone who wants to work on it.

Fix a bug, add a feature, improve the docs, or translate the bot into your language. There's a place for each of those.

## The path

The handbook follows the path you take through the codebase: set up your machine, understand how the pieces fit, build the change, then get it reviewed and merged.

<div class="grid cards" markdown>

* :fontawesome-solid-wrench: **Set up**

    ---
    Install the toolchain, link a development bot, and launch Mitup on your machine.

    [:fontawesome-solid-arrow-right: Setup](../contribute/setup.md)

* :fontawesome-solid-sitemap: **Understand**

    ---
    See how the workspace is laid out and how the apps, libraries, and tools fit together.

    [:fontawesome-solid-arrow-right: Architecture](../contribute/architecture.md)

* :fontawesome-solid-hammer: **Build**

    ---
    Add a handler, work with the data layer, log well, and run the checks as you go.

    [:fontawesome-solid-arrow-right: Adding a handler](../contribute/adding_a_handler.md)

* :fontawesome-solid-code-branch: **Contribute**

    ---
    Open an issue, branch, and take a merge request through review to merge.

    [:fontawesome-solid-arrow-right: Contribution workflow](../contribute/making_contributions.md)

</div>

## The ground rules

Four things hold across everything else in the handbook, so know them before you write any code.

* Every merge request starts from an issue a maintainer has accepted. See the [contribution workflow](../contribute/making_contributions.md).
* Run the full validation gate, `uv run mb validate`, before you open a merge request. See [testing and validation](../contribute/testing.md).
* The conventions for each area live as skills under [`.agents/skills/`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/tree/main/.agents/skills), and the handbook pages link to the ones that apply.
* You own every line you submit, whether you typed it or an assistant did. See [building with AI](../contribute/working_with_agents.md).
