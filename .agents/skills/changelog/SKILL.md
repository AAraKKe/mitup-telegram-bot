---
name: changelog
description: Changelog fragments compiled by towncrier into CHANGELOG.md. Auto-load when writing, naming or reviewing a fragment in changelog.d/, when choosing a change's type (feature, removal, change, fix, internal) or the version bump it implies, and when opening a merge request, since every MR carries a fragment.
user-invocable: false
---

# Changelog

Every merge request records what it changed in a **fragment**: a small Markdown file in
`changelog.d/` holding one or two sentences about one change. At release time towncrier
compiles the pending fragments into `CHANGELOG.md`, links each line to its MR and deletes
the fragments. The towncrier config is `[tool.towncrier]` in the root `pyproject.toml`.

## Naming

`changelog.d/<MR number>.<type>.md`, for example `changelog.d/776.fix.md`.

* A second fragment of the same type in one MR takes a counter: `776.fix.1.md`.
* `+<slug>.<type>.md` is a fragment whose MR number is filled in at release time. Only tooling
  writes it, see [Automated merge requests](#automated-merge-requests); a person names the
  fragment after the MR.
* `uv run mb ci check-changelog` validates every name and, in an MR pipeline, requires one
  fragment numbered after that MR. Release MRs are exempt from the second rule; automated MRs
  satisfy it as described in [Automated merge requests](#automated-merge-requests).

## Types

| Type | Meaning | Version bump |
|---|---|---|
| `feature` | Users gain a capability that is the first of its kind | Major |
| `removal` | Users lose a capability entirely | Major |
| `change` | An existing capability works, looks or is worded differently, or gains or loses one option | Minor |
| `fix` | Restores what was already intended | Patch |
| `internal` | Nothing a user can observe: tooling, infrastructure, dependencies, admin screens in the bot | Patch |

* A **user** is anyone using the bot in the normal way, supporters included; Telegram group
  admins are users. An **admin** is one of the bot's operators, who see the admin menu.
  Admin-only screens are `internal`.
* `feature` or `change`: first of its kind versus one more of a kind. Mirrored for removals:
  the last of its kind is a `removal`, one of several is a `change`.
* User-guide test: a `feature` needs a new section in the user guide, a `change` edits an
  existing section, `fix` and `internal` need no user-guide change.
* The type belongs to the fragment, not the MR. An MR with effects of several types carries
  one fragment per type. The release takes the highest type across all pending fragments, and
  the maintainer can override the computed version.

| Change | Type |
|---|---|
| Polls that only admins can send | `internal` |
| Creating a meeting with a date poll | `feature` |
| Choosing your date format (no formatting control existed before) | `feature` |
| A setting to receive or mute polls (notification settings already exist) | `change` |
| A link now appearing on a card | `change` |
| Removing wording from a card | `change` |
| Removing one reminder option out of several | `change` |
| Removing reminders altogether | `removal` |

## Working order

The MR number exists only once the MR is open, so:

1. Open the MR.
2. Add `changelog.d/<MR number>.<type>.md`, commit it.
3. Push. The MR's first pipeline fails `check-changelog` until this push lands.

## Automated merge requests

Renovate and the Crowdin sync commit before their MR exists, so they write their own unnumbered
`internal` fragment, `changelog.d/+<branch>-<UTC timestamp>.internal.md`. The timestamp keeps
two MRs from the same branch apart. The file opens with an HTML comment saying why its name has
no number; every tool that writes an unnumbered fragment must write that comment too.

`uv run mb release prepare` gives each of these fragments its number before it compiles the
changelog: it renames the file to `<MR number>.internal.md` and drops the comment, so the line
links to its MR like any other. The number is the `(!123)` that ends the subject of the commit
that first added the file to `main`. If that commit has no such suffix, the command refuses and
names the file to rename by hand. A comment that opens any fragment is dropped before compiling,
so a fragment renamed by hand does not leak it into the changelog.

* Renovate runs `tools/renovate_changelog_fragment.sh` through `postUpgradeTasks` in
  `renovate.json`. The self-hosted runner must allow that exact command in `allowedCommands`.
* The Crowdin sync (`tools/mb/src/mb/crowdin_mr_ops.py`) writes its fragment into the commit it
  force-pushes, so the MR always holds exactly one.
* On a `renovate/` or Crowdin branch, the check passes when the MR itself adds an unnumbered
  `internal` fragment; one that another automated MR left on `main` does not count. It compares
  the commit the pipeline runs on with its target: `CI_MERGE_REQUEST_DIFF_BASE_SHA` in a detached
  pipeline, and otherwise the first parent of that commit, which is how merged results and merge
  train pipelines are built. The CI clone holds one commit, so
  the check fetches the other one itself. A fragment numbered after the MR also passes.

## Wording

* One or two sentences, sentence case, ending with a period.
* State what is true after the change, in the present tense.
* `feature`, `removal`, `change` and `fix`: write for a user, in the bot's own vocabulary. No
  internal terms, file names or symbol names.
* `internal` may be technical.
* No MR or issue numbers in the text; the link is added automatically.

Good:

* `feature`: "You can choose how dates and times are written in your meetings."
* `change`: "Meeting cards show a link to the group the meeting belongs to."
* `fix`: "Reminders arrive at the chosen time after a daylight saving change."
* `internal`: "The CI pipeline checks that every merge request carries a changelog fragment."

Bad:

* "Fixed bug in `send_reminder` when `notification_time` is None (#412)." Names code symbols
  and an issue number, and narrates the work instead of stating the result.
* "Improve meeting cards." An imperative instead of a fact, and too vague to tell a user what
  is different.

## At release

`uv run mb release prepare` opens a release MR that numbers the unnumbered fragments, compiles
every pending fragment into a new `CHANGELOG.md` section and a new section of the What's new page, grouped as New, Removed, Changed,
Fixed and Under the hood, and deletes the fragments. Once it is merged, `uv run mb release` tags it.
The `prepare-release` skill covers both steps. Never edit `CHANGELOG.md` by hand for an unreleased
change.
