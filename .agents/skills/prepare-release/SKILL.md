---
name: prepare-release
description: Prepare a Mitup release when the maintainer asks for one. Covers running `uv run mb release prepare`, which opens the release merge request, and writing the summary for users on the What's new page (docs/whats-new/index.md). Load whenever someone asks to prepare, start, open or draft a release or a release MR, asks what the next version will be, or asks to write, review or fix a release summary or What's new entry. Agents only prepare; merging the release MR and tagging the release are the maintainer's.
user-invocable: true
---

# Prepare a release

A release takes two steps. An agent does the first one and stops.

1. **Prepare.** `uv run mb release prepare` works out the next version from the pending changelog
   fragments and opens a `release/vX.Y.Z` merge request. That MR compiles the fragments into
   `CHANGELOG.md` and adds a section for the version to `docs/whats-new/index.md`: a line reading
   `<!-- release-summary -->` with the full changelog folded underneath. The summary for users
   replaces that line.
2. **Ship.** The maintainer merges the release MR and tags it.

**Agents never merge the release MR and never run `uv run mb release` or `uv run mb deploy`. Merging
and tagging are the maintainer's alone, whatever the request says.**

Fragment types and how they decide the version are in the `changelog` skill.

## Steps

1. **Print the plan.** Run `uv run mb release prepare` with no `--yes`. It prints the previous
   version, the version it would prepare, how many fragments of each type are pending and the
   fragments that decided the version, then stops at its question without changing anything. When
   Renovate or the Crowdin sync left fragments without an MR number, it also lists the number each
   one takes. If it refuses instead, report its message and stop: nothing is pending, an earlier
   prepared release is not tagged yet, the What's new page has no place to insert the section, or
   it cannot tell which MR added an unnumbered fragment.
2. **Ask the maintainer.** Show them the version and the deciding fragments, and ask whether to go
   ahead, to use a different bump, or to wait for something that is not merged yet. Do not continue
   without an answer.
3. **Open the release MR.** Run the command again with `--yes`, plus `--patch`, `--minor` or
   `--major` if they asked for a different bump. It pushes `release/vX.Y.Z` and prints the MR link.
4. **Write the summary.** Check out `origin/release/vX.Y.Z` in a worktree (the `worktree-setup`
   skill). In `docs/whats-new/index.md`, under the new `## vX.Y.Z` heading, replace the
   `<!-- release-summary -->` line with the summary. The folded changelog right below it is the
   source; the rules are in the next section.
5. **Check and push.** Every line of the folded changelog must end with a link to its MR; report
   any that does not instead of fixing it by hand. `uv run mb ci check-changelog` and
   `uv run mb docs build` must pass. Commit and push to the release branch.
6. **Report.** Give the MR link and the summary as written, so the maintainer can read it without
   opening the diff, and say what you left out of it and why.

## Writing the summary

The summary is deliberately not exhaustive. It points out what users will notice; the complete list
is folded right below it, so leaving something out hides nothing.

* **Source**: only the fragments compiled into this version, as listed in the folded changelog.
  Never mention anything that is not in that list.
* **What goes in**: every `feature` and every `removal`. A `change` when users meet it on a screen
  they use often: the main menu, a meeting card, creating or editing a meeting, reminders and
  notifications. A `fix` only when users were visibly hitting the problem. Never `internal`. In the
  folded changelog those types sit under New, Removed, Changed, Fixed and Under the hood.
* **Merge** fragments about the same thing into one point.
* **Length**: a major release gets one opening sentence and at most five points; a minor at most
  three points; a patch one or two sentences in total. A release with nothing a user would notice
  gets exactly one sentence saying it is a maintenance release and nothing changes in how you use
  Mitup.
* **Each point** leads with what the user can now do or will see, in one or two sentences, names the
  screen where they find it, and links the user-guide section when there is one.
* **Voice**: second person, plain, in the bot's own vocabulary (load `user-facing-text` and
  `docs-style`). No MR numbers, no internal terms, no hype, no exclamation marks, no em-dashes, and
  no absolutes such as "never", "always" or "instantly" unless the code proves them.
* **Format**: short paragraphs or a short bullet list. No headings: a heading adds an entry to the
  page's table of contents.
* A major release links its blog post under `docs/blog/posts/` when one exists.

## Example

A minor release whose fragments are two `change` entries ("The main menu lists your next three
meetings." and "The address on a meeting card opens in your maps app."), one `fix` ("Editing a
meeting's title keeps its links.") and four `internal` entries.

Good:

```markdown
- The main menu lists your next three meetings, so you can open one with a single tap.
  See [Main menu](../user-guide/main_menu.md#meetings).
- On a meeting card, tap the address to open it in your maps app.
  See [Where](../user-guide/create_a_meeting.md#where).
```

Bad:

```markdown
### Highlights

Huge update! Mitup now always shows your upcoming meetings instantly, we reworked how addresses
are rendered (!812), fixed a bug in the title editor and upgraded our dependencies.
```

The bad one adds a heading to the table of contents, uses hype, an exclamation mark, absolutes the
code does not prove and an MR number, names no screen, describes the work instead of what the user
sees, and includes a fix nobody noticed and an internal change.
