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
      <div class="mitup-bot-msg"><div class="mitup-bot-msg__content"><div class="mitup-bot-msg__sender">mitupbot</div><div class="mitup-bot-msg__text"><span class="mitup-card__title">⚙️ Settings</span><hr class="mitup-card__rule"/><span class="mitup-card__section"><strong>🔣 Language</strong></span><div class="mitup-bot-msg__row" style="--cols: 3"><div class="mitup-key mitup-key--rich mitup-key--primary">🇺🇸 English</div><div class="mitup-key mitup-key--rich">🇪🇸 Spanish</div><div class="mitup-key mitup-key--rich">🇪🇸 Galician</div></div><div class="mitup-bot-msg__row" style="--cols: 3"><div class="mitup-key mitup-key--rich">🇩🇪 German</div><div class="mitup-key mitup-key--rich">🇧🇷 Portuguese</div><div class="mitup-key mitup-key--rich">🇮🇹 Italian</div></div><hr class="mitup-card__rule"/><span class="mitup-card__section"><strong>🌐 <span data-note="0">Timezone</strong></span></span> <span class="mitup-chip">✏️ Change</span><br/>Europe/Madrid<br/><br/><span class="mitup-card__section"><strong>⏰ Notifications</strong></span> <span class="mitup-chip mitup-chip--success">Enabled</span><br/>5 minutes before a meeting starts <span class="mitup-chip">✏️ Change</span><br/><br/><span class="mitup-card__section"><strong>⌛ Timeout</strong></span> <span class="mitup-chip">✏️ Change</span><br/>A meeting without an end time stays active 5 minutes after it starts.<hr class="mitup-card__rule"/><span class="mitup-card__section"><strong>👥 <span data-note="1">Default Options</strong></span></span> <span class="mitup-chip">Open</span><br/>What every meeting you create starts with.<br/><br/><span class="mitup-card__section"><strong>🛡️ Privacy</strong></span> <span class="mitup-chip">Open</span><br/>Your data, the policy, export and deletion.<hr class="mitup-card__rule"/><div class="mitup-bot-msg__row"><div class="mitup-key mitup-key--rich">≪ Main Menu</div></div></div></div></div>
    </div>
  </div>
  <span class="mitup-annotation mitup-annotation--right" data-for="0" style="top: 277px;">
    <span class="mitup-annotation__label">Change chips</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--left" data-for="1" style="top: 482px;">
    <span class="mitup-annotation__label">Defaults</span>
    <span class="mitup-annotation__line"></span>
  </span>
</div>
<!-- /mock -->

## Language

Tap another flag and every message, button and reminder switches to that language.

## Timezone

Tap *✏️ Change*{.button-like} under Timezone and send a city name, or share your location through Telegram for a more exact answer. Mitup then shows every meeting time on your clock. The location is only used for the lookup; the [privacy page](../faq/privacy.md) says exactly what is kept.

## Notifications

Reminders can be turned off, and you choose how many minutes before a meeting they arrive.

## Timeout

A meeting doesn't become inactive the second it ends. It stays active for a grace period first, counted from the end time, or from the start when the meeting has no end. That grace period is your timeout, five minutes by default. Tap *✏️ Change*{.button-like} and send the number of minutes you want, [up to a day](limits.md#the-timeout-grace-period). When it runs out the meeting moves to *💾 Past*{.button-like}, where you can bring it back.

## Default meeting options

These are the [meeting settings](meeting_settings.md) every new meeting starts with: the five switches under Behavior and the time format. Set them once and each meeting you create begins that way. You can still change any of them on a meeting's own settings afterwards, and changing your defaults never touches meetings you already made.

## Privacy

*Open*{.button-like} next to Privacy is where your data lives:

* *Read*{.button-like} opens the [privacy policy](../faq/privacy.md).
* *Export*{.button-like} sends you a copy of everything Mitup holds on you.
* *🗑️ Delete*{.button-like} removes your account and everything linked to it, after a confirmation. See [Erasure](../faq/privacy.md#your-rights-and-how-to-exercise-them) first. There is no undo.
