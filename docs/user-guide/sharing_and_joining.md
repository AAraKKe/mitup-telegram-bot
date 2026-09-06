---
description: "Post a Mitup meeting card into any Telegram chat and track the RSVPs: joining, leaving, the waiting list, and inviting a guest by name."
icon: material/share-variant-outline
---

# Sharing and joining

## Sharing your meeting

Your own copy of a meeting, the one in your chat with the bot, carries a *📨 Share*{.button-like} button. Tap it, pick any chat you're in from Telegram's picker, and the card posts there with its buttons. The bot never has to be added to that chat.

The same card can go to as many chats as you like. Every copy points at the same meeting, so a join from any chat updates the participant count everywhere at once.

Share is a shortcut for Telegram's inline mode: it fills in the bot query for you. Typing `@mitupbot` in any chat's message box reaches the same card. See [using Mitup from any chat](inline_mode.md).

### Group permissions

!!! warning "Two group permissions"

    Posting any Mitup card into a group needs the **Send Stickers & GIFs** permission, which is
    what Telegram checks for inline mode. A card with photos also needs **Send Photos**. Group
    owners and admins have both; for everyone else the group's permissions decide. Without the
    first, the inline menu keeps loading and nothing posts. Without the second, a card with photos
    is refused while one without goes through.

## The shared card

Once a meeting lands in a chat, anyone there can act on it without opening a private chat with the bot first.

<!-- mock:shared_card -->
<div class="mitup-annotated">
  <div class="mitup-annotated__chat">
    <div class="mitup-chat-header">
      <div class="mitup-chat-header__back">‹</div>
      <div class="mitup-avatar"><img src="../../assets/images/brand/mark-256.png" alt="Mitup"></div>
      <div>
        <div class="mitup-chat-header__name">mitupbot</div>
        <div class="mitup-chat-header__sub">bot · online</div>
      </div>
    </div>
    <div class="mitup-annotated__body">
      <div class="mitup-bot-msg"><div class="mitup-bot-msg__content"><div class="mitup-bot-msg__sender">mitupbot</div><div class="mitup-bot-msg__text"><span class="mitup-card__title">Weekend hike prep</span><span class="mitup-card__footer" data-note="0">Created by: Ana Marín · <span class="mitup-chip mitup-chip--success mitup-chip--muted">Public</span></span><hr class="mitup-card__rule"/><span class="mitup-card__section">📄 <strong>Description</strong></span><br/>Bring water and sturdy boots.<hr class="mitup-card__rule"/><span class="mitup-card__section">🕒 <strong>When</strong></span><br/>Tue, <span class="mitup-time">Sep 1</span>, <span class="mitup-time">18:00</span> - <span class="mitup-time">21:00</span><br/><br/><span class="mitup-card__section">🗺️ <strong>Where</strong></span><br/>Trailhead car park<div class="mitup-card__map"></div><hr class="mitup-card__rule"/><span class="mitup-card__section">👥 <strong>Participants</strong></span> · 6 of 20<ul><li>Ana Marín</li><li>Diego</li><li>Sara</li><li>Tomás</li><li>Lucía</li></ul>😴 Waiting list · 1<ul><li>Marta</li></ul><hr class="mitup-card__rule"/><span class="mitup-card__footer">🔍 <span data-note="1">Not searchable</span> in this chat yet<br/>Built with <span class="mitup-chip">Mitup</span></span></div></div><div class="mitup-bot-msg__keyboard"><div class="mitup-bot-msg__row" style="--cols: 3"><div class="mitup-key mitup-key--success" data-note="2">✅ Join</div><div class="mitup-key">😄 Invite</div><div class="mitup-key mitup-key--danger">❌ Leave</div></div><div class="mitup-bot-msg__row"><div class="mitup-key">📨 Share</div></div><div class="mitup-bot-msg__row"><div class="mitup-key">Make it searchable</div></div></div></div>
    </div>
  </div>
  <span class="mitup-annotation mitup-annotation--left" data-for="0" style="top: 122px;">
    <span class="mitup-annotation__label">Who made it</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--left" data-for="1" style="top: 675px;">
    <span class="mitup-annotation__label">Searchable?</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--right" data-for="2" style="top: 727px;">
    <span class="mitup-annotation__label">RSVP row</span>
    <span class="mitup-annotation__line"></span>
  </span>
</div>
<!-- /mock -->

A Public tag next to the owner's name means the meeting can be passed on: *📨 Share*{.button-like} sits under the card only on a [public](meeting_settings.md#public) meeting. *😄 Invite*{.button-like} is there only when the owner allows [open invitations](meeting_settings.md#open-invitations).

The date and time are written in the owner's timezone, with the zone named when the owner turned on [show timezone](meeting_settings.md#time-format). On Telegram clients that support it, tapping a date or a time shows the moment on your own clock.

### Photos on a card

A [Host](limits.md#photos-on-the-meeting-card) can put up to five photos on a meeting. They sit at the top of the card, one on its own or several as a collage or a slideshow. A card with photos needs one more [group permission](#group-permissions) to post.

## Joining and leaving

Tap *✅ Join*{.button-like} and you're on the list: the count ticks up, your name appears, and every copy of the card updates. *❌ Leave*{.button-like} takes you back off.

Two things can stop a join. If the meeting is full and has no [waiting list](meeting_settings.md#waiting-list), the bot says so and nothing changes. If the owner turned on [lock on start](meeting_settings.md#lock-on-start), the RSVP row disappears while the meeting is underway, so no one can join or leave.

## The waiting list

The owner can cap how many people fit and turn on a [waiting list](meeting_settings.md#waiting-list) for the overflow. With it on, a join on a full meeting lands you in the queue, which shows under its own line on the card. The count on the Participants line covers both, so a full meeting with people waiting reads as bigger than its cap.

The queue moves on its own, in the order people joined. When a confirmed participant leaves, the freed spot goes to whoever has waited longest, and the bot messages them to say so. If several spots open at once, that many people move up in one pass.

## Inviting a guest

Some people won't tap a button: a dead phone, no Telegram account, someone who asked you in person to sign them up. *😄 Invite*{.button-like} covers that. If you tapped it from a group, the bot asks you to continue in your private chat, then asks for a name. Send it, confirm, and that person joins as a plain name on the list, marked as invited by you. They don't need an account, and nothing is sent to them. Anyone the card reaches can invite, as long as the meeting still has room.

## Make it searchable

*Make it searchable*{.button-like} ties a shared card to the chat it sits in. After that, anyone in the chat can pull the meeting back up through [Mitup's inline mode](inline_mode.md#meetings-in-this-chat) without scrolling back to find the original message. The card says so in its closing line and the button goes away.

## Receiving a card with no account

A shared card works for someone who has never opened Mitup. Tapping *✅ Join*{.button-like} signs them up from their Telegram profile and adds them to the meeting; a popup on the card confirms it and points them at [@mitupbot](https://t.me/mitupbot?start=src_web). Leaving works the same way. Reminders, editing, and creating meetings of their own live in the private chat with the bot.
