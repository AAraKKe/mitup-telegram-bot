---
description: "Set your language, timezone, reminders, timeout, and default meeting options in Mitup, plus export or delete the data it holds on you."
icon: material/cog-outline
---

# Your settings

Open *⚙️ Settings*{.button-like} from the main menu. Everything about your account is on one card, and each setting carries its own chip next to the value it changes.

<!-- mock:user_settings -->
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
      <div class="mitup-bot-msg"><div class="mitup-bot-msg__content"><div class="mitup-bot-msg__sender">mitupbot</div><div class="mitup-bot-msg__text"><span class="mitup-card__title">⚙️ Settings</span><hr class="mitup-card__rule"/><span class="mitup-card__section"><strong>🔣 <span data-note="0">Language</strong></span></span><div class="mitup-bot-msg__row" style="--cols: 3"><div class="mitup-key mitup-key--rich mitup-key--primary">🇺🇸 English</div><div class="mitup-key mitup-key--rich">🇪🇸 Spanish</div><div class="mitup-key mitup-key--rich">🇪🇸 Galician</div></div><div class="mitup-bot-msg__row" style="--cols: 3"><div class="mitup-key mitup-key--rich">🇩🇪 German</div><div class="mitup-key mitup-key--rich">🇧🇷 Portuguese</div><div class="mitup-key mitup-key--rich">🇮🇹 Italian</div></div><hr class="mitup-card__rule"/><span class="mitup-card__section"><strong>🌐 <span data-note="1">Timezone</strong></span></span> <span class="mitup-chip">✏️ Change</span><br/>Europe/Madrid<hr class="mitup-card__rule"/><span class="mitup-card__section"><strong>⏰ <span data-note="2">Notifications</strong></span></span> <span class="mitup-chip mitup-chip--success">Enabled</span><br/><br/><span class="mitup-card__section"><strong>Reminder</strong></span> <span class="mitup-chip mitup-chip--success">Enabled</span> <span class="mitup-chip">✏️ Change</span><br/>5 minutes before a meeting starts<br/><br/><span class="mitup-card__section"><strong>Deletion warning</strong></span> <span class="mitup-chip mitup-chip--success">Enabled</span><br/>7 days before a meeting is permanently deleted<br/><br/><span class="mitup-card__section"><strong>Deletion notice</strong></span> <span class="mitup-chip mitup-chip--success">Enabled</span><br/>Once a meeting is deleted<hr class="mitup-card__rule"/><span class="mitup-card__section"><strong>⌛ <span data-note="3">Timeout</strong></span></span> <span class="mitup-chip">✏️ Change</span><br/>A meeting without an end time stays active 5 minutes after it starts.<hr class="mitup-card__rule"/><span class="mitup-card__section"><strong>👥 <span data-note="4">Default Options</strong></span></span> <span class="mitup-chip">Open</span><br/>What every meeting you create starts with.<hr class="mitup-card__rule"/><span class="mitup-card__section"><strong>🛡️ <span data-note="5">Privacy</strong></span></span> <span class="mitup-chip">Open</span><br/>Your data, the policy, export and deletion.<hr class="mitup-card__rule"/><div class="mitup-bot-msg__row"><div class="mitup-key mitup-key--rich">≪ Main Menu</div></div></div></div></div>
    </div>
  </div>
  <span class="mitup-annotation mitup-annotation--left" data-for="0" style="top: 154px;">
    <span class="mitup-annotation__label">Language</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--right" data-for="1" style="top: 277px;">
    <span class="mitup-annotation__label">Timezone</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--left" data-for="2" style="top: 353px;">
    <span class="mitup-annotation__label">Notifications</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--right" data-for="3" style="top: 575px;">
    <span class="mitup-annotation__label">Timeout</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--left" data-for="4" style="top: 669px;">
    <span class="mitup-annotation__label">Default options</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--right" data-for="5" style="top: 745px;">
    <span class="mitup-annotation__label">Privacy</span>
    <span class="mitup-annotation__line"></span>
  </span>
</div>
<!-- /mock -->

## Language

Tap another flag and every message, button and reminder switches to that language.

## Timezone

Tap *✏️ Change*{.button-like} under Timezone and send a city name, or share your location through Telegram for a more exact answer. Mitup then shows every meeting time on your clock. The location is only used for the lookup; the [privacy page](../faq/privacy.md) says exactly what is kept.

## Notifications

Mitup sends you three notifications, each with its own switch. A reminder before a meeting starts, where you also choose how many minutes ahead it arrives. A warning a week before one of your inactive meetings is removed for good. A confirmation once it is gone. All three start on, and the switch on the section header turns them all on or off at once. Silencing the two deletion notifications changes nothing about the deletion itself, which runs on [its own schedule](meeting_lifecycle.md#how-long-an-inactive-meeting-is-kept).

## Timeout

A meeting doesn't become inactive the second it ends. It stays active for a grace period first, counted from the end time, or from the start when the meeting has no end. That grace period is your timeout, five minutes by default. Tap *✏️ Change*{.button-like} and send the number of minutes you want, [up to a day](limits.md#the-timeout-grace-period). When it runs out the meeting moves to *💾 Past*{.button-like}, where you can bring it back.

## Default meeting options

These are the [meeting settings](meeting_settings.md) every new meeting starts with: the five switches under Behavior and the time format. Set them once and each meeting you create begins that way. You can still change any of them on a meeting's own settings afterwards, and changing your defaults never touches meetings you already made.

## Privacy

*Open*{.button-like} next to Privacy is where your data lives:

* *Read*{.button-like} opens the [privacy policy](../faq/privacy.md).
* *Export*{.button-like} sends you a copy of everything Mitup holds on you.
* *🗑️ Delete*{.button-like} removes your account and everything linked to it, after a confirmation. See [Erasure](../faq/privacy.md#your-rights-and-how-to-exercise-them) first. There is no undo.
