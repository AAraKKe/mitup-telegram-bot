---
description: "When a Mitup meeting turns inactive, what happens to the shared cards, how to reactivate it, and how long it is kept before deletion."
icon: material/calendar-clock-outline
---

# Meeting lifecycle

Every meeting has two lives. While it's active, people can join, leave, and see it in the cards you shared into your chats. Once it finishes it becomes inactive: read-only, kept under *💾 Past*{.button-like} until you reactivate it, delete it, or the time Mitup keeps it for runs out.

<div class="mlc">
  <div class="mlc__row">
    <div class="mlc__stage mlc__stage--active">
      <span class="mlc__badge">Active</span>
      <span class="mlc__desc">People can join, leave, and see it in your shared cards.</span>
    </div>
    <div class="mlc__arrow">
      <span class="mlc__arrow-glyph">&rarr;</span>
      <span class="mlc__arrow-label">a few minutes after it finishes</span>
    </div>
    <div class="mlc__stage mlc__stage--inactive">
      <span class="mlc__badge">Inactive</span>
      <span class="mlc__desc">Kept in your Past meetings for 90 days, a year for Gamemasters and Commissioners.</span>
    </div>
    <div class="mlc__arrow">
      <span class="mlc__arrow-glyph">&rarr;</span>
      <span class="mlc__arrow-label">if you don't bring it back</span>
    </div>
    <div class="mlc__stage mlc__stage--deleted">
      <span class="mlc__badge">Deleted</span>
      <span class="mlc__desc">Removed permanently.</span>
    </div>
  </div>
  <div class="mlc__notes">
    <div class="mlc__note mlc__note--loop">Reactivate takes an inactive meeting back to Active as a fresh start: same details, a fresh sign-up list, no date yet.</div>
    <div class="mlc__note mlc__note--flag">A week before deletion the bot sends a one-time heads-up with a button to reactivate.</div>
  </div>
</div>

## When a meeting becomes inactive

You don't mark a meeting as finished by hand. Mitup does it once the meeting's clock has run out and your [timeout](settings.md#timeout) has passed on top of it:

* **Start and end time set.** The timeout counts from the end. Start to end can [cover a week at most](limits.md#how-long-one-meeting-can-last).
* **Start time only.** The timeout counts from the start, so the meeting goes inactive shortly after it begins. Set an end time to keep it joinable through the evening.
* **No date at all.** It stays active so you can add a date later, for [90 days, or a year for Gamemasters and Commissioners](limits.md#meetings-with-no-date).

## If you leave the bot

Blocking the bot or deleting your chat with it doesn't pull you out of your meetings: you stay the owner of the ones you made and on the lists of the ones you joined. Meetings with no date that you own close a month after you created them or last brought them back, since nobody is left to add the date. Send `/start` and you're back, with your language and timezone where you left them.

## What happens when it becomes inactive

* The cards you shared into chats keep their layout, lose their buttons and say the meeting has finished.
* The meeting moves to *💾 Past*{.button-like}, where you can still open it and read everything you wrote.
* Its date and time stay as you set them, so the meeting reads as it happened.
* The participant list is cleared, guests you invited by name included.

## Reminders around the start

Everyone on the list with notifications on gets two reminders, you included if you joined: one ahead of the start, as many minutes before as each person chose under [Notifications](settings.md#notifications), and one when the meeting starts. Both carry the schedule and the place, and the first lets you leave from the card if your plans changed.

<!-- mock:reminder -->
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
      <div class="mitup-bot-msg"><div class="mitup-bot-msg__content"><div class="mitup-bot-msg__sender">mitupbot</div><div class="mitup-bot-msg__text"><span class="mitup-card__title">⏰ Starting soon</span>Weekend hike prep<br/><span data-note="0">Starts in</span> 15 minutes<hr class="mitup-card__rule"/><span class="mitup-card__section">🕒 <strong>When</strong></span><br/>Tue, <span class="mitup-time">Sep 1</span>, <span class="mitup-time">18:00</span> - <span class="mitup-time">21:00</span><br/><br/><span class="mitup-card__section">🗺️ <strong>Where</strong></span><br/>Trailhead car park<div class="mitup-card__map"></div><hr class="mitup-card__rule"/>Can't make it? <span class="mitup-chip mitup-chip--danger" data-note="1">❌ Leave</span><hr class="mitup-card__rule"/><div class="mitup-bot-msg__row"><div class="mitup-key mitup-key--rich mitup-key--primary">📂 Open meeting</div></div><div class="mitup-bot-msg__row"><div class="mitup-key mitup-key--rich">≪ Main Menu</div></div></div></div></div>
    </div>
  </div>
  <span class="mitup-annotation mitup-annotation--left" data-for="0" style="top: 135px;">
    <span class="mitup-annotation__label">Counts down</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--right" data-for="1" style="top: 407px;">
    <span class="mitup-annotation__label">Can't make it</span>
    <span class="mitup-annotation__line"></span>
  </span>
</div>
<!-- /mock -->

The schedule is written in the owner's timezone, the same as on the shared card. Tap the date or the time and your client shows it on your own clock.

## Reactivating an inactive meeting

Open *💾 Past*{.button-like} from the main menu, then *Open*{.button-like} on the meeting you want.

<!-- mock:past_meeting -->
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
      <div class="mitup-bot-msg"><div class="mitup-bot-msg__content"><div class="mitup-bot-msg__sender">mitupbot</div><div class="mitup-bot-msg__text">This meeting is no longer active. Reactivate it to share it again, or delete it permanently.<hr class="mitup-card__rule"/><span class="mitup-card__title">Weekend hike prep</span><span class="mitup-card__footer">Created by: Ana Marín</span><hr class="mitup-card__rule"/><span class="mitup-card__section">📄 <strong>Description</strong></span><br/>Bring water and sturdy boots.<hr class="mitup-card__rule"/><span class="mitup-card__section">🕒 <strong>When</strong></span><br/>Tue, <span class="mitup-time">Sep 1</span>, <span class="mitup-time">18:00</span> - <span class="mitup-time">21:00</span><br/><em data-note="0">This meeting has finished.</em><br/><br/><span class="mitup-card__section">🗺️ <strong>Where</strong></span><br/>Trailhead car park<div class="mitup-card__map"></div><hr class="mitup-card__rule"/><span class="mitup-card__section">👥 <strong>Participants</strong></span> · 3 of 20<ul><li>Ana Marín</li><li>Diego</li><li>Sara</li></ul><hr class="mitup-card__rule"/><div class="mitup-bot-msg__row" style="--cols: 2"><div class="mitup-key mitup-key--rich" data-note="1">Reactivate meeting</div><div class="mitup-key mitup-key--rich">🗑️ Delete</div></div><div class="mitup-bot-msg__row"><div class="mitup-key mitup-key--rich">≪ 💾 Past meetings</div></div></div></div></div>
    </div>
  </div>
  <span class="mitup-annotation mitup-annotation--left" data-for="0" style="top: 357px;">
    <span class="mitup-annotation__label">Finished</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--right" data-for="1" style="top: 682px;">
    <span class="mitup-annotation__label">Bring it back</span>
    <span class="mitup-annotation__line"></span>
  </span>
</div>
<!-- /mock -->

*Reactivate meeting*{.button-like} brings it back as a fresh start, not a rerun. Everything you wrote stays: title, description, location, language and every option you picked. The date, the time and lock on start are cleared, and the sign-up list starts empty, so share the card again and everyone taps *✅ Join*{.button-like} for the new round.

A reactivated meeting counts towards your [active meetings](limits.md#active-meetings) again and is off the deletion clock below. Until you give it a date it is a meeting with no date, with [that window](limits.md#meetings-with-no-date) to set one.

## Deleting a meeting

You can delete an active meeting from its card with *🗑️ Delete*{.button-like}, an inactive one from the screen above, or either straight from its list. Mitup asks you to confirm before anything happens.

To clear the whole backlog in one go, open *💾 Past*{.button-like} and use *🗑️ Delete all past meetings*{.button-like} at the end of the list. Mitup tells you how many meetings that is and waits for *Delete them all*{.button-like}. It takes every inactive meeting you own and leaves the active ones alone.

!!! warning "Deletion is permanent"

    Deleting a meeting removes it right away, with no grace period and no undo. The meeting,
    its participant list, and the invited-only guests attached to it are gone for good. If you
    might want it back, reactivate it instead of deleting it. Clearing the whole list at once
    works the same way, on every inactive meeting you have.

## How long an inactive meeting is kept

Mitup keeps an inactive meeting for 90 days, or a year if you're a [Gamemaster or a Commissioner](limits.md#how-long-a-finished-meeting-is-kept), counting from the day it became inactive. Then it's deleted permanently.

A week before that, the bot sends you a one-time heads-up naming the meeting, with a *Reactivate meeting*{.button-like} button. Reactivating takes the meeting off the clock entirely. A second message tells you once the meeting is gone.

Each of those two messages has its own switch under [Notifications](settings.md#notifications), and both start on. Turn one off if you would rather not hear about it.

!!! warning "The deletion doesn't wait for the heads-up"

    The heads-up is a message from the bot, so it only reaches you if the bot can still write to
    you. If you blocked it, deleted the chat, or turned the message off, it doesn't arrive and
    the deletion happens on schedule anyway. Unblock the bot and send `/start` if you want to
    keep getting those.
