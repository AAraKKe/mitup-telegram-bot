---
icon: material/tag-outline
---

# Versioning

A version says how much the product changed. Every merge request records its changes as changelog fragments, each with a type, and the release takes its version bump from the highest type among the pending fragments. Milestones are named after versions (`vX.Y.Z`), so the type of a change also tells you which milestone its issue lands in.

## The five types

| Type | Meaning | Version bump |
|---|---|---|
| `feature` | Users gain a capability that is the first of its kind | Major |
| `removal` | Users lose a capability entirely | Major |
| `change` | An existing capability works, looks or is worded differently, or gains or loses one option | Minor |
| `fix` | Restores what was already intended | Patch |
| `internal` | Nothing a user can observe: tooling, infrastructure, dependencies, admin screens in the bot | Patch |

A user is anyone using the bot in the normal way, supporters and Telegram group admins included. Screens only the bot's operators can open are `internal`.

To tell a `feature` from a `change`, ask whether it is the first of its kind or one more of a kind. Choosing a date format was a `feature`, because nothing controlled formatting before; a switch to mute polls is a `change`, because notification settings already exist. Removals mirror this: removing reminders altogether is a `removal`, removing one reminder option out of several is a `change`.

A quick check: a `feature` needs a new section in the user guide, a `change` edits an existing one, and `fix` and `internal` need none.

## Writing a fragment

A fragment is a file in `changelog.d/` named after your merge request and its type, such as `changelog.d/776.fix.md`, holding one or two sentences about the change. The number only exists once the merge request is open, so open it first, then add the fragment and push. An MR with effects of several types adds one fragment per type.

Write what is true after the change, in the present tense. For every type except `internal`, write for someone using the bot: no code names, file names or issue numbers. The link to your merge request is added for you.

The [`changelog` skill](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/changelog/SKILL.md) has the full naming and wording rules with examples.

## How a version is chosen

* A capability built behind a flag gets its fragment from the merge request that exposes it to users, not from the one that lands the code.
* The maintainer can override the computed version.

Cutting releases and deploying is maintainer work. A release starts as its own merge request, which compiles the pending fragments into `CHANGELOG.md` and the What's new page, and is tagged once that merge request is merged. This page exists so that fragment types and milestones make sense as you contribute.
