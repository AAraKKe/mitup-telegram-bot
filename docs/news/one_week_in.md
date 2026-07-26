---
icon: material/newspaper-variant-outline
hide:
    - toc
---

# One week in

<span class="news-meta">26 July 2026</span>

The new Mitup has been carrying every meeting for a bit over a week now. The old bot is off, and the most interesting thing I have to report is that there was not a lot to report. That is what I wanted from a migration.

You may have seen small issues here and there. We have been fixing a few bugs around shared meetings and how conversations are handled, but overall I am pretty happy with the state of things.

With the cutover behind us, this week went into something quieter: tuning how long a meeting lives, and what happens to it at the end.

## Meetings that know when to stop

A meeting with no date is a draft: you type a title, mean to pick a Saturday for it, and then you don't. That is how we see meetings without a date. You are still creating it, and you have not picked a when before sharing it. Drafts used to live almost forever, and that is not sustainable at the scale Mitup runs at now.

A draft's lifetime now matches how far ahead you can actually schedule: 90 days on a free or Brewer account, a year for Gamemasters and Commissioners. Bringing a draft back from *💾 Your past meetings*{.button-like} restarts that clock, which it never used to do. That was another bug fixed this week: before it, you could reactivate a meeting and in less than a minute, puf, back to inactive.

The grace period that keeps a meeting active after it finishes, your *⌛ Timeout*{.button-like}, now stops at a day. There was no ceiling before, so a big enough number kept every meeting you own active forever: never finished, never tidied away. It was one of the favourite abuse tactics on the old bot, and it carried over in the migration. Mitup was cluttered with meetings that would never end, and that slows things down for everyone; the database cannot simply keep scaling, so it gets boundaries instead. Five minutes is still the default, and a day covers leaving a meeting up through the day after it ended.

## Reactivating starts a meeting over

Reactivating a past meeting used to hand it back with the date it already had, so it came back finished and got closed out again almost immediately. It is a restart now.

The title, description, location, language, and every option come back exactly as you left them. The date and time are cleared, so you pick the new one, and the sign-up list starts fresh for the new round. Bringing back last month's board game night is a couple of taps, a new date, and one more share into the group.

## A year of past meetings on the bigger tiers

Finished meetings used to be kept 180 days for everyone. That is now 90 days on a free or Brewer account, and a year for Gamemasters and Commissioners. Storing them is a real part of the bill, and that is the part Hosts cover, so the longer window is theirs.

A year is the number that matters for anything annual: the summer tournament, the Christmas dinner, the birthday drinks you organize every July. When it comes around again, open *💾 Your past meetings*{.button-like}, reactivate last year's meeting, set the date, share it. Nothing to retype.

On a free account, 90 days still leaves a whole season to notice a meeting and bring it back.

## Deletion you can see coming

A week before a finished meeting reaches the end of that window, the bot sends you one message to let you know it is happening, with a button to bring it back. One message, a week's notice, and then the meeting goes.

That message is a DM like any other, so it only reaches you if the bot can still write to you. If you blocked it, the message doesn't arrive and the deletion happens on schedule anyway, which is why the windows on the [meeting lifecycle](../user-guide/meeting_lifecycle.md) page are the ones to plan around. The per-tier numbers are on [Limits and Host perks](../user-guide/limits.md).

If something looks off in your own past meetings, email [support@mitup.social](mailto:support@mitup.social) and I'll take a look.

Juanpe.
