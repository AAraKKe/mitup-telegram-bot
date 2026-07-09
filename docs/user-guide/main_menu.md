---
icon: material/menu
---

# Main menu

After registration, you'll see the main menu. This is the hub for everything in Mitup.

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
            <strong>Welcome to Mitup Bot!</strong><br/><br/>Choose one of the following options:
          </div>
        </div>
        <div class="mitup-bot-msg__keyboard">
          <div class="mitup-bot-msg__row"><div class="mitup-key">➕ New meeting</div></div>
          <div class="mitup-bot-msg__row"><div class="mitup-key">📂 Your active meetings</div></div>
          <div class="mitup-bot-msg__row"><div class="mitup-key">💾 Your past meetings</div></div>
          <div class="mitup-bot-msg__row mitup-bot-msg__row--2">
            <div class="mitup-key">👥 Joined meetings</div>
            <div class="mitup-key">⚙️ Settings</div>
          </div>
          <div class="mitup-bot-msg__row mitup-bot-msg__row--2">
            <div class="mitup-key">❓ Help</div>
            <div class="mitup-key">♥ Collaborate</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>

The buttons represent all actions you can take in Mitup. There are no slash commands except `/main_menu`, which returns you to this menu.

Available actions:

* *➕ New meeting*{.button-like}: Create a new meeting, configure it, and share. See [Create a meeting](create_a_meeting.md).
* *📂 Your active meetings*{.button-like}: Meetings you've created that are still active. Meetings become inactive a set time after they happen.
* *💾 Your past meetings*{.button-like}: Inactive meetings you haven't deleted yet.
* *👥 Joined meetings*{.button-like}: Meetings you joined but don't own. Useful if you need to find a meeting you were sent but can't locate the original message. You can view these but not edit them.
* *⚙️ Settings*{.button-like}: Configure Mitup. See [Settings](settings.md).
* *❓ Help*{.button-like}: Opens the [user guide](getting_started.md) on this site in your browser.
* *♥ Collaborate*{.button-like}: Support Mitup on Patreon and link your Patreon account, which switches on your Host badge and higher limits. See [supporting Mitup](../collaborate/donation.md) and [limits and Host perks](limits.md).

!!! info "Meeting lifecycle"

    Once a meeting is created, Mitup tracks its existence until it is deleted either by the owner or automatically. To better understand when a meeting transitions from active to inactive or when it is permanently deleted, see [Meeting lifecycle](meeting_lifecycle.md).
