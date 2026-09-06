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
* Each of your own active meetings, one row apiece. Tap one and the full card posts into the chat.

<!-- mock:inline_results -->
<div class="mitup-annotated">
  <div class="mitup-annotated__chat">
    <div class="mitup-chat-header">
      <div class="mitup-chat-header__back">‹</div>
      <div class="mitup-avatar">🏔️</div>
      <div>
        <div class="mitup-chat-header__name">Hiking crew</div>
        <div class="mitup-chat-header__sub">6 members</div>
      </div>
    </div>
    <div class="mitup-annotated__body">
      <div class="mitup-inline-results"><div class="mitup-inline-top">➕ Create a new meeting</div><div class="mitup-inline-result"><div class="mitup-inline-result__thumb">🔍</div><div class="mitup-inline-result__body"><div class="mitup-inline-result__title">🔍 <span data-note="0">Meetings in this chat</span></div><div class="mitup-inline-result__sub">Search for meetings shared in this chat</div></div></div><div class="mitup-inline-result"><div class="mitup-inline-result__thumb">W</div><div class="mitup-inline-result__body"><div class="mitup-inline-result__title" data-note="1">Weekend hike prep</div><div class="mitup-inline-result__sub">👥 (6/20)<br/>🕒 Tue, Sep 1, 18:00</div></div></div><div class="mitup-inline-result"><div class="mitup-inline-result__thumb">B</div><div class="mitup-inline-result__body"><div class="mitup-inline-result__title">Book club</div><div class="mitup-inline-result__sub">👥 (6/20)<br/>🕒 Fri, Sep 18, 18:00</div></div></div></div>
    </div>
  </div>
  <span class="mitup-annotation mitup-annotation--right" data-for="0" style="top: 124px;">
    <span class="mitup-annotation__label">Chat's meetings</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--left" data-for="1" style="top: 176px;">
    <span class="mitup-annotation__label">Your meetings</span>
    <span class="mitup-annotation__line"></span>
  </span>
</div>
<!-- /mock -->

The list holds your own active meetings. A public meeting someone shared with you is passed on from its own *📨 Share*{.button-like} button instead. Tapping a meeting here posts the same [card](sharing_and_joining.md#the-shared-card) the Share button produces.

In a group, inline mode needs a permission; if the menu keeps loading, see [group permissions](sharing_and_joining.md#group-permissions).

## Meetings in this chat

The *🔍 Meetings in this chat*{.button-like} entry is the other half of [Make it searchable](sharing_and_joining.md#make-it-searchable). When someone attaches a shared meeting to a chat with *Make it searchable*{.button-like}, it joins that chat's searchable set. After that, anyone in the chat can pull it back up here without scrolling to find the original message.

Tap the entry, then *🔍 Load meetings*{.button-like} and *🔍 Search meetings*{.button-like} on the message it posts, and inline mode reopens on this chat's meetings so you can pick one and post it fresh.

!!! tip "Nothing shared yet"

    If no meeting has been made searchable in the chat, the results say so. Share a meeting into the chat and tap *Make it searchable*{.button-like} first, then it'll turn up here.
