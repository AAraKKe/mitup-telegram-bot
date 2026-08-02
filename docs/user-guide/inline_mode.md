---
description: "Type @mitupbot in any Telegram chat to post one of your meetings or pull up the meetings already shared there. No bot in your group."
icon: material/text-box-search-outline
---

# Using Mitup from any chat

You don't have to open your chat with the bot to drop a meeting into a group. Type `@mitupbot` in the message box of any chat, wait a beat, and a little menu of Mitup results pops up above the keyboard. Pick one and it posts straight into that chat. The bot never has to be a member of the group for this to work.

This is Telegram's inline mode, and Mitup uses it for two things: posting one of your own meetings into a conversation, and pulling back up the meetings that were already shared there.

## What the menu shows

Type `@mitupbot` followed by a space and leave the rest empty. The results that appear are:

* A button along the top that opens your private chat with the bot, labelled *➕ Create a new meeting*{.button-like} if you already use Mitup, or *🚀 Explore Mitup*{.button-like} if you don't.
* A *🔍 Meetings in this chat*{.button-like} entry, which pulls up meetings that were made searchable in this chat. More on that below.
* Each of your own active meetings, as a ready-to-post card. Tap one and the full card, with its buttons, posts into the chat.

<div class="mitup-annotated">
  <div class="mitup-annotated__chat">
    <div class="mitup-chat-header">
      <div class="mitup-chat-header__back">‹</div>
      <div class="mitup-avatar">🏔️</div>
      <div>
        <div class="mitup-chat-header__name">Hiking Crew</div>
        <div class="mitup-chat-header__sub">6 members</div>
      </div>
    </div>
    <div class="mitup-annotated__body">
      <div class="mitup-inline-results">
        <div class="mitup-inline-top">➕ Create a new meeting</div>
        <div class="mitup-inline-result">
          <div class="mitup-inline-result__thumb">🔍</div>
          <div class="mitup-inline-result__body">
            <div class="mitup-inline-result__title">🔍 Meetings in this chat</div>
            <div class="mitup-inline-result__sub">Search for meetings shared in this chat</div>
          </div>
        </div>
        <div class="mitup-inline-result">
          <div class="mitup-inline-result__thumb">W</div>
          <div class="mitup-inline-result__body">
            <div class="mitup-inline-result__title">Weekend Hike Prep · Sat, 12 Jul, 09:00</div>
            <div class="mitup-inline-result__sub">👥 4 participants</div>
          </div>
        </div>
        <div class="mitup-inline-result">
          <div class="mitup-inline-result__thumb">B</div>
          <div class="mitup-inline-result__body">
            <div class="mitup-inline-result__title">Board Game Night · Fri, 18 Jul, 20:00</div>
            <div class="mitup-inline-result__sub">👥 6 participants</div>
          </div>
        </div>
      </div>
    </div>
    <div class="mitup-chat-input">
      <div class="mitup-chat-input__menu">≡</div>
      <span class="mitup-chat-input__attach">📎</span>
      <span class="mitup-chat-input__placeholder">@mitupbot </span>
    </div>
  </div>
  <span class="mitup-annotation mitup-annotation--right" style="top: 132px;">
    <span class="mitup-annotation__label">Chat's meetings</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--left" style="top: 187px;">
    <span class="mitup-annotation__label">Your meetings</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--left" style="top: 298px;">
    <span class="mitup-annotation__label">Type @mitupbot</span>
    <span class="mitup-annotation__line"></span>
  </span>
</div>

Only meetings you can share show up as cards: your own, or public ones you've been passed. A meeting someone else made and kept private won't appear in your list.

## It's the same card as Share

Tapping one of your meetings here posts the exact card the *📨 Share*{.button-like} button produces. The [Share button](sharing_and_joining.md#sharing-your-meeting) is the shortcut: it fills in the `@mitupbot` query for you and opens the chat picker, so you skip typing the handle. Both paths end at the same posted card, with the same RSVP buttons. Use whichever is closer to hand.

## Meetings in this chat

The *🔍 Meetings in this chat*{.button-like} entry is the other half of [Make it searchable](sharing_and_joining.md#make-it-searchable). When someone attaches a shared meeting to a chat with *Make it searchable*{.button-like}, it joins that chat's searchable set. After that, anyone in the chat can pull it back up here without scrolling to find the original message.

Tap the entry to post a short message with a *🔍 Load meetings*{.button-like} button. Tapping that swaps in a *🔍 Search meetings*{.button-like} button, which reopens inline mode already pointed at this chat's meetings, so you can pick the one you're after and post it fresh.

!!! tip "Nothing shared yet"

    If no meeting has been made searchable in the chat, the results say so. Share a meeting into the chat and tap *Make it searchable*{.button-like} first, then it'll turn up here.
