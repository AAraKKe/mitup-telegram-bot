---
description: "Create a meeting in Telegram for any event: add a title, a date, a place, a participant limit, and a language, then share the card."
icon: material/calendar-plus-outline
---

# Create a meeting

Creating a meeting takes a title and a couple of taps. Everything else, the date, the location, who can join, is optional and lives one screen away in the edit hub.

## Start a new meeting

1. Tap *➕ New meeting*{.button-like} from the main menu.
2. Send the title you want. Real ones work best: "Weekend Hike Prep", "Board Game Night", "Ana's Birthday Drinks".
3. Mitup creates the meeting and applies your [default meeting options](settings.md#default-meeting-options).

That's enough to have a shareable meeting. To add a date, a place, or a participant limit, keep going into the edit hub.

!!! tip "Set the date while you type the title"

    Send the title with a Telegram date attached and Mitup reads it straight into the meeting, so you skip the date step entirely. Telegram renders the date as a tappable link while you compose the message.

## The edit hub

After creation you land on the edit hub. Every part of the meeting is a button here, and you can come back to it anytime with *✏️ Edit*{.button-like}.

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
          <div class="mitup-bot-msg__text">
            Meeting created: <strong>Weekend Hike Prep</strong><br/><br/>You can add more information to the meeting with the options below. The information which has not been added won't be shown when the meeting is shared.<br/><br/>When finished click on ✅ Done
          </div>
        </div>
        <div class="mitup-bot-msg__keyboard">
          <div class="mitup-bot-msg__row mitup-bot-msg__row--2">
            <div class="mitup-key">🅰️ Title</div>
            <div class="mitup-key">📄 Description</div>
          </div>
          <div class="mitup-bot-msg__row"><div class="mitup-key">🕒 When</div></div>
          <div class="mitup-bot-msg__row mitup-bot-msg__row--2">
            <div class="mitup-key">👥 Participants</div>
            <div class="mitup-key">🗺️ Location</div>
          </div>
          <div class="mitup-bot-msg__row mitup-bot-msg__row--2">
            <div class="mitup-key">🔣 Language</div>
            <div class="mitup-key">⚙️ Settings</div>
          </div>
          <div class="mitup-bot-msg__row"><div class="mitup-key">✅ Done</div></div>
          <div class="mitup-bot-msg__row"><div class="mitup-key">≪ Main Menu</div></div>
        </div>
      </div>
    </div>
  </div>
  <span class="mitup-annotation mitup-annotation--left" style="top: 144px;">
    <span class="mitup-annotation__label">Meeting card</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--right" style="top: 334px;">
    <span class="mitup-annotation__label">Edit options</span>
    <span class="mitup-annotation__line"></span>
  </span>
</div>

Each button opens one part of the meeting:

* *🅰️ Title*{.button-like}: change the meeting name.
* *📄 Description*{.button-like}: add context, an agenda, or a what-to-bring note.
* *🕒 When*{.button-like}: set the date, time, and duration. See [When: date, time, and duration](#when-date-time-and-duration).
* *👥 Participants*{.button-like}: set a maximum, or leave it open. On a free account a meeting holds up to 20 people either way; see [limits](limits.md#participants-per-meeting). You can also take someone off the list from here; the bot asks you to confirm before removing them.
* *🗺️ Location*{.button-like}: give the place a name, and for a physical spot, share the location too. See [Location](#location).
* *🔣 Language*{.button-like}: choose the language the meeting is shared in. See [Meeting language](#meeting-language).
* *⚙️ Settings*{.button-like}: four on/off options for this meeting, the [waiting list, public, open invitations, and incognito](meeting_settings.md).
* *✅ Done*{.button-like}: leave the edit hub and show the finished meeting card.

Anything you leave blank stays hidden when the meeting is shared, so a bare title-only meeting is a valid meeting.

## When: date, time, and duration

Tap *🕒 When*{.button-like} to set when the meeting happens. Its start time, end time, and lock behaviour all live on this one screen, and it fills in as you go.

The When menu starts almost empty and grows a button at a time:

* **The first time you open it**, there is one button, *▶️ Set start time*{.button-like}. Pick the date on the calendar, then send the time in `HH:MM`. A meeting can have a start time and nothing else.
* **Once a start time exists**, more buttons appear. *⏹️ Set end time*{.button-like} gives the meeting a duration, and the end must be after the start. The *🔴 Lock on start*{.button-like} toggle shows up here too. *🗑️ Clear times*{.button-like} removes the times again and turns the meeting back into a placeholder, after you confirm.
* **With both times set**, the screen shows the full start and end summary, like the one below, and you can change either time.

<div class="mitup-phone">
  <div class="mitup-phone__screen">
    <div class="mitup-phone__status">
      <span>9:41</span>
      <div class="mitup-phone__notch"></div>
      <span class="mitup-phone__signal"><span>5G</span><span class="mitup-phone__battery"></span></span>
    </div>
    <div class="mitup-chat-header">
      <div class="mitup-chat-header__back">‹</div>
      <div class="mitup-avatar"><img src="../../assets/images/brand/mark-256.png" alt="Mitup"></div>
      <div>
        <div class="mitup-chat-header__name">mitupbot</div>
        <div class="mitup-chat-header__sub">bot · online</div>
      </div>
    </div>
    <div class="mitup-phone__body">
      <div class="mitup-bot-msg">
        <div class="mitup-bot-msg__content">
          <div class="mitup-bot-msg__sender">mitupbot</div>
          <div class="mitup-bot-msg__text">
            Meeting starts at: <strong>Sat, 12 Jul, 10:00</strong><br/>Meeting ends at: <strong>Sat, 12 Jul, 14:00</strong><br/><br/>You can change the times or lock the attendees list when the meeting starts, so no one can join or leave once it is underway.
          </div>
        </div>
        <div class="mitup-bot-msg__keyboard">
          <div class="mitup-bot-msg__row mitup-bot-msg__row--2">
            <div class="mitup-key">▶️ Set start time</div>
            <div class="mitup-key">⏹️ Set end time</div>
          </div>
          <div class="mitup-bot-msg__row"><div class="mitup-key">🔴 Lock on start</div></div>
          <div class="mitup-bot-msg__row"><div class="mitup-key">🗑️ Clear times</div></div>
          <div class="mitup-bot-msg__row"><div class="mitup-key">≪ ✏️ Edit</div></div>
        </div>
      </div>
    </div>
    <div class="mitup-chat-input">
      <div class="mitup-chat-input__menu">≡</div>
      <span class="mitup-chat-input__attach">📎</span>
      <span class="mitup-chat-input__placeholder">Write a message…</span>
    </div>
  </div>
</div>

Whether you give the meeting an end time changes when it becomes inactive after it happens. See [meeting lifecycle](meeting_lifecycle.md). How far ahead the start date can be depends on your account: 90 days on a free account, more as a [Host](limits.md#scheduling-ahead). The end can be up to [a week after the start](limits.md#how-long-one-meeting-can-last), which covers a festival or a weekend away in a single meeting.

### Lock on start

Turn on *🔴 Lock on start*{.button-like} in the When menu and joining and leaving freeze once the meeting is underway. With an end time set, the freeze covers the window between start and end and lifts at the end. Without one, it starts at the start time and stays on until you turn the lock off or the meeting goes inactive. See [lock on start](meeting_settings.md#lock-on-start) for the full behaviour.

!!! note "Lock needs a start time"

    Lock on start only appears once the meeting has a start time, since that's the moment the freeze begins. Clearing the times also clears the lock.

## Location

Open *🗺️ Location*{.button-like} in the edit hub and you get two ways to say where the meeting is:

* *🅰️ Name*{.button-like}: type a place name. Anything works: "The Usual Pub", "Park Entrance by the Fountain", or a video-call link for a meeting that isn't anywhere physical.
* *📍 Location*{.button-like}: share a map location from your phone. Tap the attachment icon, choose Location, and send any spot, not only where you are right now. This one is phone-only, since Telegram only offers location sharing on mobile.

You can set just a name, just a location, or both. When you've shared a location, the meeting card carries an *📍 Open in Maps*{.button-like} button, so anyone looking at it can open the spot in their maps app.

## Meeting language

By default a meeting uses your own language, the one from your [settings](settings.md#language). To share a meeting in another language, open *🔣 Language*{.button-like} in the edit hub and pick one. That choice only affects this meeting: the buttons and labels people see on the shared card.

Your own language still controls the menus you navigate. You can run Mitup in English and share a meeting that greets your friends in Spanish.
