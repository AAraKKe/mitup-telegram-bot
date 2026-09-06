---
description: "Why the Mitup inline menu keeps loading in a group, why your meeting card looks out of date, why an edit vanished, and who can edit a meeting."
icon: material/wrench-outline
---

# When Mitup looks broken

Most of the moments that feel like a bug are Mitup doing what it should. Here are the common ones.

## Mitup says I've already answered this

You tapped a button on an old message and got a popup saying that question is closed. The buttons on that message belong to an earlier question, one you already answered or walked away from, so the bot clears them. Scroll to the most recent message from mitupbot and continue from there.

## My half-finished edit vanished

You were partway through editing a meeting, opened the main menu or sent something else, and came back to find that step gone.

This only happens while the bot is waiting for a typed answer. If you tap away to the main menu or send an unrelated message right then, Mitup lets go of the half-typed answer and there's nothing left to save. Tapping buttons is never affected. Start that step again and Mitup picks up from the meeting's current state.

## The inline menu spins, or the card won't post in a group

That's a group permission, not the bot. Inline mode in a group needs the **Send Stickers & GIFs** permission, and a card with photos also needs **Send Photos**. Group owners and admins have both, which is why it works for whoever set the group up and not for everyone else. Ask an admin to turn them on for members. See [group permissions](../user-guide/sharing_and_joining.md#group-permissions).

## The card in my chat with the bot looks out of date

Cards sitting in a group redraw themselves as people join and leave. [Your own copy](../user-guide/create_a_meeting.md#the-meeting-card), the one you open from *📂 Active*{.button-like} or *👥 Joined*{.button-like}, doesn't. Tap *🔄 Refresh*{.button-like} on it.

## The meeting card is in the wrong language

The language on a shared meeting card is the meeting's language, and only the owner controls it. If the owner never picked one, the card falls back to the owner's own language. Your personal language setting only changes the private replies mitupbot sends you, not the card that everyone sees. If you own the meeting, open it, tap *⚙️ Settings*{.button-like} and pick a flag; see [meeting language](../user-guide/meeting_settings.md#language).

## I can't edit this meeting

Only the owner of a meeting can edit it. If you joined a meeting someone else created, you can see it and change your own RSVP, but the title, time, location, and options belong to the owner. If you tap an edit action on a meeting you don't own, Mitup sends you back to the main menu. To run your own version, create a new meeting and invite the same people.

## Still stuck

If none of these matches what you're seeing, email [support@mitup.social](mailto:support@mitup.social). A screen recording of what happens helps a lot, since it shows the exact steps that led there.

Not sure whether what you're seeing is meant to work that way? You can ask that in the [community group on Telegram](https://t.me/mitupgroup) too, if you'd rather ask in public than write an email.
