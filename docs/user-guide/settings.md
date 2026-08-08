---
description: "Set your language, timezone, reminders, timeout, and default meeting options in Mitup, plus export or delete the data it holds on you."
icon: material/cog-outline
---

# Settings

Open *⚙️ Settings*{.button-like} from the main menu to set your language, timezone, reminders, and the defaults every new meeting starts from.

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
      <div class="mitup-bot-msg">
        <div class="mitup-bot-msg__content">
          <div class="mitup-bot-msg__sender">mitupbot</div>
          <div class="mitup-bot-msg__text">Configure MitUp.</div>
        </div>
        <div class="mitup-bot-msg__keyboard">
          <div class="mitup-bot-msg__row mitup-bot-msg__row--2">
            <div class="mitup-key">🔣 Language</div>
            <div class="mitup-key">⌛ Timeout</div>
          </div>
          <div class="mitup-bot-msg__row mitup-bot-msg__row--2">
            <div class="mitup-key">⏰ Notifications</div>
            <div class="mitup-key">🌐 Timezone</div>
          </div>
          <div class="mitup-bot-msg__row mitup-bot-msg__row--2">
            <div class="mitup-key">👥 Default Options</div>
            <div class="mitup-key">🛡️ Privacy</div>
          </div>
          <div class="mitup-bot-msg__row">
            <div class="mitup-key">≪ Main Menu</div>
          </div>
        </div>
      </div>
    </div>
  </div>
  <span class="mitup-annotation mitup-annotation--left" style="top: 60px;">
    <span class="mitup-annotation__label">Description</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--right" style="top: 120px;">
    <span class="mitup-annotation__label">Your settings</span>
    <span class="mitup-annotation__line"></span>
  </span>
</div>

## Language

Tap *🔣 Language*{.button-like} to pick your language. Every bot message, button, and reminder switches to it.

## Timezone

Tap *🌐 Timezone*{.button-like} to set where you are. Send a city name or share your location through Telegram, and Mitup shows every meeting time in your local hours. This keeps things straight when you join a meeting someone set up on the other side of the world.

## Reminders

Tap *⏰ Notifications*{.button-like} to turn reminders on or off and choose how many minutes before a meeting you want the nudge.

## Timeout

Tap *⌛ Timeout*{.button-like} to set how long a meeting stays in your active list after it finishes. Mitup counts from the end time, or from the start time if you didn't set an end. When that grace period passes, the meeting becomes inactive and moves to *💾 Past meetings*{.button-like}, where you can reactivate it.

Send the number of minutes you want. It's five by default, and [a day (1440 minutes) is the ceiling](limits.md#the-timeout-grace-period), so a meeting can stay up through the day after it ended but not longer.

## Default meeting options

Tap *👥 Default Options*{.button-like} to choose what every new meeting you create starts with: waiting list, public, open invitations, incognito, and lock on start. Set them once and each meeting you make begins the same way. You can still change any option on an individual meeting afterwards, and changing your defaults never touches meetings you already made.

For what each option does, see [Meeting settings](meeting_settings.md).

## Privacy

Tap *🛡️ Privacy*{.button-like} to manage the data Mitup keeps about you:

* *🛡️ Privacy policy*{.button-like} opens the [privacy policy](../faq/privacy.md) in your browser. It describes exactly what Mitup stores and the rights you have over it.
* *📦 Export my data*{.button-like} sends you a JSON file with a copy of everything Mitup stores about you.
* *🗑️ Delete my data*{.button-like} permanently deletes your account and everything linked to it, after a double confirmation. See [Erasure](../faq/privacy.md#your-rights-and-how-to-exercise-them) before you tap it. There is no undo.
