---
description: "The Mitup main menu: a New meeting button, your active, joined and past meeting lists, and the way to settings, help and collaborate."
icon: material/menu
---

# Main menu

After registration you land on the main menu, and every screen in Mitup has a way back to it.

<!-- mock:main_menu -->
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
      <div class="mitup-bot-msg"><div class="mitup-bot-msg__content"><div class="mitup-bot-msg__sender">mitupbot</div><div class="mitup-bot-msg__text"><span class="mitup-card__title">Welcome to Mitup!</span>Create meetings and share them with your friends.<div class="mitup-bot-msg__row"><div class="mitup-key mitup-key--rich mitup-key--primary" data-note="0">➕ New meeting</div></div><hr class="mitup-card__rule"/><span class="mitup-card__section">🗓️ <strong>Meetings</strong></span><div class="mitup-bot-msg__row" style="--cols: 2"><div class="mitup-key mitup-key--rich" data-note="1">📂 Active · 2</div><div class="mitup-key mitup-key--rich">👥 Joined · 1</div></div><div class="mitup-bot-msg__row"><div class="mitup-key mitup-key--rich">💾 Past · 3</div></div><hr class="mitup-card__rule"/><span class="mitup-card__section">⚙️ <strong>Account</strong></span><div class="mitup-bot-msg__row" style="--cols: 2"><div class="mitup-key mitup-key--rich" data-note="2">⚙️ Settings</div><div class="mitup-key mitup-key--rich">❓ Help</div></div><div class="mitup-bot-msg__row"><div class="mitup-key mitup-key--rich">♥ Collaborate</div></div></div></div></div>
    </div>
  </div>
  <span class="mitup-annotation mitup-annotation--left" data-for="0" style="top: 163px;">
    <span class="mitup-annotation__label">New meeting</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--right" data-for="1" style="top: 252px;">
    <span class="mitup-annotation__label">Your lists</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--left" data-for="2" style="top: 375px;">
    <span class="mitup-annotation__label">Account</span>
    <span class="mitup-annotation__line"></span>
  </span>
</div>
<!-- /mock -->

Mitup has two commands, `/start` and `/main_menu`, and both bring this menu up. Everything else is a tap.

## New meeting

*➕ New meeting*{.button-like} starts a meeting from a title. [Create a meeting](create_a_meeting.md) walks through it.

## Meetings

Your meetings sit in three lists, each with its count next to the label. A list with nothing in it is greyed out.

* *📂 Active*{.button-like}: meetings you created that are still active.
* *👥 Joined*{.button-like}: meetings you joined but don't own. Handy when the message with the card is buried in a chat.
* *💾 Past*{.button-like}: your inactive meetings, the ones you haven't deleted yet.

<!-- mock:active_list -->
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
      <div class="mitup-bot-msg"><div class="mitup-bot-msg__content"><div class="mitup-bot-msg__sender">mitupbot</div><div class="mitup-bot-msg__text"><span class="mitup-card__title">📂 Active meetings</span><strong data-note="0">Weekend hike prep</strong><br/>🕒 Tue, <span class="mitup-time">Sep 1</span>, <span class="mitup-time">18:00</span> - <span class="mitup-time">21:00</span><br/>🗺️ Trailhead car park<br/>👥 6 of 20<div class="mitup-bot-msg__row" style="--cols: 2"><div class="mitup-key mitup-key--rich mitup-key--primary" data-note="1">Open</div><div class="mitup-key mitup-key--rich mitup-key--danger">🗑️ Delete</div></div><hr class="mitup-card__rule"/><div class="mitup-bot-msg__row"><div class="mitup-key mitup-key--rich">≪ Main Menu</div></div></div></div></div>
    </div>
  </div>
  <span class="mitup-annotation mitup-annotation--left" data-for="0" style="top: 118px;">
    <span class="mitup-annotation__label">One meeting</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--right" data-for="1" style="top: 197px;">
    <span class="mitup-annotation__label">Open or delete</span>
    <span class="mitup-annotation__line"></span>
  </span>
</div>
<!-- /mock -->

*Open*{.button-like} brings up [the meeting's card](create_a_meeting.md#the-meeting-card). The lists of meetings you own also carry *🗑️ Delete*{.button-like} next to it, which asks you to confirm. Long lists come in pages, with arrows under the last entry.

## Account

* *⚙️ Settings*{.button-like}: your language, timezone, reminders, and the defaults every new meeting starts from. See [Your settings](settings.md).
* *❓ Help*{.button-like}: links to this guide, the community group and the news channel, plus the support address.
* *♥ Collaborate*{.button-like}: back Mitup on Patreon and link your Patreon account, which switches on your Host badge and perks. See [supporting Mitup](../collaborate/donation.md) and [limits and Host perks](limits.md).

!!! info "Meeting lifecycle"

    Mitup tracks a meeting until it is deleted, either by you or automatically. For when a meeting
    turns from active to inactive, and when it is removed for good, see
    [Meeting lifecycle](meeting_lifecycle.md).
