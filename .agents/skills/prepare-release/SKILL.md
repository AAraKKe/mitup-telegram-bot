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
   opening the diff. List what you left out in one line each, and name any fix you were unsure
   about so the maintainer can ask for it.

## Writing the summary

Write it the way you would tell the community group what is new: a short post from the people who
make Mitup to the people who use it to meet their friends. It is not a record of the work that went
into the release. The complete list is folded right below it, so leaving something out hides
nothing, and a summary that tries to cover everything is the most common way to get this wrong.

### What goes in

* **Source**: only the fragments compiled into this version, as listed in the folded changelog,
  where the types sit under New, Removed, Changed, Fixed and Under the hood. Say nothing the
  fragments do not say: no advice, no reasons and no claims of your own.
* Every `feature` and every `removal`.
* A `change` only when users meet it on a screen they use often: the main menu, a meeting card,
  creating or editing a meeting, reminders and notifications.
* A `fix` only when people were running into the problem: a user reported it, or it broke one of
  those often-used screens for everyone. Most fixes stay out. If you cannot tell, leave it out and
  name it in your report.
* Never `internal`.
* Fragments about the same thing become one point. Unrelated fragments are never joined into one
  point to fit more in.

### How long

| Release | Shape | Words a reader sees |
|---|---|---|
| Major | One opening sentence, then at most four points | 100 |
| Minor | At most three points | 60 |
| Patch | One or two sentences | 30 |
| Nothing a user would notice | One sentence: it is a maintenance release and nothing changes in how you use Mitup | 20 |

* A point is one sentence of at most 25 words.
* Count the words before you push. When the summary is over its limit, drop the point users would
  notice least; do not squeeze the same content into fewer words.
* The opening sentence of a major release says what the release means for the person using Mitup,
  not what it contains.

### How it reads

* **Lead with what the user can now do or will see.** Name the screen, and make its name the link
  to the user-guide section when there is one. No separate "See ..." sentence.
* **One idea per point.** Leave out the cases it covers, how it works, what used to happen and why
  it changed. "Links in a meeting's description can be tapped on the meeting card" is the whole
  point; which kinds of link are recognised belongs to the changelog.
* **Test each point**: would someone who uses Mitup with their friends mention this to another
  member? If not, cut it.
* **Voice**: second person, plain and friendly, in the bot's own vocabulary (load
  `user-facing-text` and `docs-style`). No MR numbers, no internal terms, no hype, no exclamation
  marks, no em-dashes, and no absolutes such as "never", "always" or "instantly" unless the code
  proves them.
* **Format**: short paragraphs or a short bullet list. No headings: a heading adds an entry to the
  page's table of contents.
* A major release links its blog post under `docs/blog/posts/` when one exists.

## Example

A minor release whose fragments are two `change` entries ("The main menu lists your next three
meetings." and "The address on a meeting card opens in your maps app, for addresses typed by hand
and for pinned locations."), two `fix` entries ("Editing a meeting's title keeps its links." and
"A reminder set for a meeting that is deleted afterwards is cancelled, whichever way the meeting
was deleted.") and four `internal` entries.

Good, 31 words:

```markdown
- The [main menu](../user-guide/main_menu.md#meetings) lists your next three meetings, so you can
  open one with a single tap.
- Tap the address on a [meeting card](../user-guide/create_a_meeting.md#where) to open it in your
  maps app.
```

Bad, a recital of the release:

```markdown
- The main menu lists your next three meetings. Open one with a single tap. Meetings further ahead
  are still under My meetings. See [Main menu](../user-guide/main_menu.md#meetings).
- The address on a meeting card opens in your maps app, both for addresses typed by hand and for
  pinned locations. See [Where](../user-guide/create_a_meeting.md#where).
- Editing a meeting's title keeps its links, and a reminder for a meeting that is deleted
  afterwards is cancelled, whichever way the meeting was deleted.
```

Every sentence in it is true, and it is still wrong: it runs to 73 words where 60 is the limit, its
points take two and three sentences, it adds a sentence no fragment says ("Meetings further
ahead..."), it lists the cases a change covers, it spends "See ..." sentences on links, and its
last point joins two unrelated fixes nobody was running into.

Bad, an announcement:

```markdown
### Highlights

Huge update! Mitup now always shows your upcoming meetings instantly, we reworked how addresses
are rendered (!812), and upgraded our dependencies.
```

It adds a heading to the table of contents, uses hype, an exclamation mark, absolutes the code does
not prove and an MR number, names no screen, describes the work instead of what the user sees, and
includes an internal change.
