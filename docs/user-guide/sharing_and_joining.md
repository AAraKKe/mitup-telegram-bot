---
description: "Post a Mitup meeting card into any Telegram chat and track the RSVPs: joining, leaving, the waiting list, and inviting a guest by name."
icon: material/share-variant-outline
---

# Sharing and joining

A meeting only matters once other people can see it. This page follows a meeting out of the owner's chat and into a group: how you post it, how it looks once it lands, how someone taps to join, how a guest with no Telegram account still ends up on the list, and what happens when the last spot fills up.

## Sharing your meeting

Your own copy of a meeting, the one in your chat with the bot, always carries a *📨 Share*{.button-like} button. Tap it and Telegram opens its chat picker. Pick any chat you're in and the card posts there with its buttons intact. You never have to add the bot to that chat.

The same card can go to as many chats as you like. Every copy points at the same meeting, so a join from any chat updates the participant count everywhere at once.

Share is a shortcut for Telegram's inline mode: it fills in the bot query for you. You can reach the same card by typing `@mitupbot` in any chat's message box. See [using Mitup from any chat](inline_mode.md).

## The shared card

Once a meeting lands in a chat, it arrives as a single message with its own buttons. Anyone in that chat can act on it without opening a private chat with the bot first.

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
            <strong>Weekend Hike Prep</strong> (Created by: Marta)<br/>
            --- 📄 Sorting out gear and carpools before Saturday.<br/>
            --- 🕒 Sat, 12 Jul 2026, 09:00<br/>
            --- 🗺️ Northside trailhead<br/>
            --- 👥 4 Participants (Max: 6)<br/>
            &nbsp;&nbsp;Marta<br/>
            &nbsp;&nbsp;Diego<br/>
            &nbsp;&nbsp;Sara<br/>
            &nbsp;&nbsp;Tomás<br/><br/>
            🔍 Make this meeting searchable in this chat.
          </div>
        </div>
        <div class="mitup-bot-msg__keyboard">
          <div class="mitup-bot-msg__row mitup-bot-msg__row--3">
            <div class="mitup-key">✅ Join</div>
            <div class="mitup-key">😄 Invite</div>
            <div class="mitup-key">❌ Leave</div>
          </div>
          <div class="mitup-bot-msg__row">
            <div class="mitup-key">📨 Share</div>
          </div>
          <div class="mitup-bot-msg__row">
            <div class="mitup-key">Make it searchable</div>
          </div>
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

The card shows the title, who made it, the description, the date in your own timezone, the location, and the running participant list. The buttons below it change what the chat can do with the meeting, one row at a time.

A card that is already sitting in a chat shows its own *📨 Share*{.button-like} button only when the owner has left the meeting [public](meeting_settings.md#public). That is what lets anyone who can see the card pass it along to other chats. When the meeting isn't public, the button isn't there and the card stays in the chats it was already sent to.

!!! tip "One meeting, many chats"

    The same card can live in several chats at once. Every copy points at the same meeting, so a join from any chat updates the participant count everywhere the card was shared.

## Joining and leaving

Tap *✅ Join*{.button-like} and you're on the list. The count ticks up, your name appears, and the change shows in every chat the card was shared to. The bot answers with a short confirmation:

!!! quote "You're in"

    You joined the meeting!

Changed your mind? *❌ Leave*{.button-like} takes you back off, and the bot confirms you have left the meeting. Tapping *✅ Join*{.button-like} twice does nothing beyond the first tap. The bot tells you you're already on the list rather than adding you again.

The RSVP row also carries *😄 Invite*{.button-like} when the owner has [open invitations](meeting_settings.md#open-invitations) turned on. It's for signing up someone who can't tap Join themselves. There's more on that below.

Two things can stop a join. If the meeting is full and has no [waiting list](meeting_settings.md#waiting-list), the bot says so and nothing changes. If the owner turned on [lock on start](meeting_settings.md#lock-on-start), the RSVP row disappears once the meeting is underway, so no one can join or leave. With an end time set that lasts until the end; without one it lasts until the owner unlocks the meeting.

## The waiting list

The owner can cap how many people fit and turn on a [waiting list](meeting_settings.md#waiting-list) for the overflow. With it on, a join on a full meeting still works. It just lands you in the queue instead:

!!! quote "Added to the waiting list"

    The meeting is full. You have been added to the waiting list.

The waiting list shows on the card under its own heading, below the confirmed participants. Order matters: people are queued in the order they joined.

The queue moves on its own. The moment a confirmed participant taps *❌ Leave*{.button-like}, the freed spot goes to whoever has waited longest, and the bot notifies them straight away:

!!! quote "Promoted from the waiting list"

    There is an open spot in the meeting **Weekend Hike Prep**. You have now been promoted from the waiting list!

If several spots open at once, the bot promotes that many people from the front of the queue in one pass. Nobody has to re-tap anything.

!!! note "No waiting list, no queue"

    Without a [waiting list](meeting_settings.md#waiting-list), a join on a full meeting is turned away and there's nothing to promote from.

## Inviting a guest

Some people won't tap a button. A phone is dead, someone doesn't use Telegram at all, someone asked you in person to sign them up. *😄 Invite*{.button-like} covers all of that, as long as the owner has [open invitations](meeting_settings.md#open-invitations) turned on. With that option off, the button isn't on the card.

Tap *😄 Invite*{.button-like}. If you tapped it from a group, the bot asks you to continue in your private chat, then prompts for a name:

!!! quote "Add to guest list"

    **Add to Guest List**

    Please reply with the name of the person you want to add.

Send the name, confirm, and that person joins the meeting as a plain name on the list. They don't need an account, and nothing gets sent to them. On the card, an invited guest shows up marked as *invited by* you, so the group can see who's vouching for whom. Inviting is open to anyone the card reaches, not just the owner, as long as the meeting still has room.

## Make it searchable

*Make it searchable*{.button-like} ties a shared card to the chat it's sitting in. Tap it once and the meeting joins that chat's searchable set:

!!! quote "Now searchable"

    ✅ Now Searchable!

    This meeting is now attached to this chat. It will be included in your search results when you look for meetings using the bot's inline mode.

After that, anyone in the chat can pull the meeting back up through [Mitup's inline mode](inline_mode.md#meetings-in-this-chat), without scrolling back to find the original message. The footnote under the card flips from *Make this meeting searchable in this chat* to a line confirming it's now searchable, and the button drops off since there's nothing left to do. Tapping it on a card that's already attached tells you so.

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
            <strong>Weekend Hike Prep</strong> (Created by: Marta)<br/>
            --- 📄 Sorting out gear and carpools before Saturday.<br/>
            --- 🕒 Sat, 12 Jul 2026, 09:00<br/>
            --- 🗺️ Northside trailhead<br/>
            --- 👥 4 Participants (Max: 6)<br/>
            &nbsp;&nbsp;Marta<br/>
            &nbsp;&nbsp;Diego<br/>
            &nbsp;&nbsp;Sara<br/>
            &nbsp;&nbsp;Tomás<br/><br/>
            🔍 Make this meeting searchable in this chat.
          </div>
        </div>
        <div class="mitup-bot-msg__keyboard">
          <div class="mitup-bot-msg__row mitup-bot-msg__row--3">
            <div class="mitup-key">✅ Join</div>
            <div class="mitup-key">😄 Invite</div>
            <div class="mitup-key">❌ Leave</div>
          </div>
          <div class="mitup-bot-msg__row">
            <div class="mitup-key">📨 Share</div>
          </div>
          <div class="mitup-bot-msg__row">
            <div class="mitup-key">Make it searchable</div>
          </div>
        </div>
      </div>
    </div>
  </div>
  <span class="mitup-annotation mitup-annotation--left" style="top: 188px;">
    <span class="mitup-annotation__label">Meeting card</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--right" style="top: 316px;">
    <span class="mitup-annotation__label">RSVP row</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--left" style="top: 350px;">
    <span class="mitup-annotation__label">Share</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--right" style="top: 385px;">
    <span class="mitup-annotation__label">Searchable</span>
    <span class="mitup-annotation__line"></span>
  </span>
</div>

## Receiving a card with no account

A shared card works for someone who has never opened Mitup. When they tap *✅ Join*{.button-like}, the bot signs them up from their Telegram profile and adds them to the meeting in one step. It can't send them a private message, because it has no way to reach someone who has never opened it. What they get instead is a popup on the card, confirming they're in and pointing them at [@mitupbot](https://t.me/mitupbot?start=src_web):

!!! quote "Welcome aboard"

    You have joined the meeting, Tomás! It seems you have never used Mitup before, open a chat with [@mitupbot](https://t.me/mitupbot) to receive notifications and create new meetings!

They don't have to open it. The join already counted, and leaving works the same way if they change their mind. The one thing a brand-new participant can't do from the card is manage the meeting. Reminders, editing, and creating meetings of their own all live in the private chat with the bot, which is exactly what that popup points them to.
