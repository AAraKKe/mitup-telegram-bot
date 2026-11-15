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
* A change with no MR (rare, maintainer only) uses `+<slug>.<type>.md`; it renders without a link.
* `uv run mb ci check-changelog` validates every name and, in an MR pipeline, requires one
  fragment numbered after that MR. Only a release MR (`release/*`), which deletes the fragments
  it compiles, is exempt from the second rule.

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

`uv run mb release prepare` opens a release MR that compiles every pending fragment into a new
`CHANGELOG.md` section and a new section of the What's new page, grouped as New, Removed, Changed,
Fixed and Under the hood, and deletes the fragments. Once it is merged, `uv run mb release` tags it.
The `prepare-release` skill covers both steps. Never edit `CHANGELOG.md` by hand for an unreleased
change.
