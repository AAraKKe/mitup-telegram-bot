---
description: "Why a Mitup button says you already answered, why an edit vanished, why a card is in another language, and who can edit a meeting."
icon: material/wrench-outline
---

# When Mitup looks broken

Most of the moments that feel like a bug are Mitup doing exactly what it should. Here are the common ones and what's really happening.

## Mitup says I've already answered this

You tapped a button on a message and got a small popup along the lines of "You've already answered this question." That message is an old one, and its buttons no longer belong to any conversation.

Mitup keeps track of the flow you're currently in. When you tap a button that belongs to a flow you already finished or abandoned, there's nothing left for it to act on, so the bot tells you the question is closed and clears the leftover buttons from that message. Scroll to the most recent message from mitupbot and continue from there.

## My half-finished edit vanished

You were partway through editing a meeting, opened the main menu or sent something else, and came back to find that step gone.

This only happens while the bot is waiting for you to *type* something: a title, a description, a location, a maximum number of people, a date, or a guest's name. If you tap away to the main menu or send an unrelated message right then, Mitup lets go of the half-typed answer and there's nothing left to save.

Tapping buttons is never affected. Toggles, navigation, joining or leaving, sharing, and confirming all act the moment you tap, so nothing is lost there. It also doesn't happen at every step of an edit, only the ones where you're typing an answer. Start that step again and Mitup picks up from the meeting's current state.

## The meeting card is in the wrong language

The language on a shared meeting card is not your language. It's the meeting's language, and only the owner controls it.

Every meeting carries its own language, set by the person who created it. If the owner never picked one, the card falls back to the owner's own language. Your personal language setting only changes the private replies mitupbot sends you, not the card that everyone sees. If you own the meeting and want to change it, open the meeting, tap *✏️ Edit*{.button-like}, and change the meeting's *🔣 Language*{.button-like}.

!!! note "Two different language settings"

    The one in *⚙️ Settings*{.button-like} is yours and only affects your private chat with the bot. The one inside a meeting's *✏️ Edit*{.button-like} screen belongs to the meeting and changes the shared card for everyone.

## I can't edit this meeting

Only the owner of a meeting can edit it. If you joined a meeting someone else created, you can see it and change your own RSVP, but the title, time, location, and options belong to the owner.

If you tap an edit action on a meeting you don't own, Mitup sends you back to the main menu instead. That's the expected outcome, not a failure. To run your own version, create a new meeting and invite the same people.

## Still stuck

If none of these matches what you're seeing, email [support@mitup.social](mailto:support@mitup.social). A screen recording of what happens helps a lot, since it shows the exact steps that led there.

Not sure whether what you're seeing is meant to work that way? You can ask that in the [community group on Telegram](https://t.me/mitupgroup) too, if you'd rather ask in public than write an email.
