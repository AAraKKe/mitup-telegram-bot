---
description: "Mitup 2.0 is here: every screen rebuilt on Telegram's new message format, photos on meetings for Hosts, and the first of many updates to come."
icon: material/newspaper-variant-outline
hide:
    - toc
---

# Mitup 2.0

<span class="news-meta">13 September 2026</span>

Mitup moved to a new platform in July. Same bot, same meetings, new machinery underneath. The point was never the move itself, it was what the move would let us do next.

We have been listening to what people ask for over the years, and a lot of it was never hard to picture. It was hard to add to the old bot without breaking something else, so much of it waited.

Every change now gets tested against everything that already works before it reaches your chats. Mitup 2.0 is the first update built that way, and it will not be the last.

## A new look

Telegram added a new kind of message this summer, one that carries real structure: headings, sections, dividers, and buttons that live inside the text instead of in a slab underneath it. Every screen Mitup sends you is drawn with them now.

So a screen reads as a card. Each list carries its count, each setting sits next to the chip that changes it, and the [user guide](../user-guide/index.md) walks through all of them.

<div class="mitup-two-up">
  <div class="mitup-two-up__item">
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
          <div class="mitup-bot-msg"><div class="mitup-bot-msg__content"><div class="mitup-bot-msg__sender">mitupbot</div><div class="mitup-bot-msg__text"><span class="mitup-card__title">Welcome to Mitup!</span>Create meetings and share them with your friends.<div class="mitup-bot-msg__row"><div class="mitup-key mitup-key--rich mitup-key--primary">➕ New meeting</div></div><hr class="mitup-card__rule"/><span class="mitup-card__section">🗓️ <strong>Meetings</strong></span><div class="mitup-bot-msg__row" style="--cols: 2"><div class="mitup-key mitup-key--rich">📂 Active · 2</div><div class="mitup-key mitup-key--rich">👥 Joined · 1</div></div><div class="mitup-bot-msg__row"><div class="mitup-key mitup-key--rich">💾 Past · 3</div></div><hr class="mitup-card__rule"/><span class="mitup-card__section">⚙️ <strong>Account</strong></span><div class="mitup-bot-msg__row" style="--cols: 2"><div class="mitup-key mitup-key--rich">⚙️ Settings</div><div class="mitup-key mitup-key--rich">❓ Help</div></div><div class="mitup-bot-msg__row"><div class="mitup-key mitup-key--rich">♥ Collaborate</div></div></div></div></div>
        </div>
      </div>

    </div>
    <span class="mitup-two-up__caption">The main menu</span>
  </div>
  <div class="mitup-two-up__item">
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
          <div class="mitup-bot-msg"><div class="mitup-bot-msg__content"><div class="mitup-bot-msg__sender">mitupbot</div><div class="mitup-bot-msg__text"><span class="mitup-card__title">⚙️ Settings</span><hr class="mitup-card__rule"/><span class="mitup-card__section"><strong>🔣 Language</strong></span><div class="mitup-bot-msg__row" style="--cols: 3"><div class="mitup-key mitup-key--rich mitup-key--primary">🇺🇸 English</div><div class="mitup-key mitup-key--rich">🇪🇸 Spanish</div><div class="mitup-key mitup-key--rich">🇪🇸 Galician</div></div><div class="mitup-bot-msg__row" style="--cols: 3"><div class="mitup-key mitup-key--rich">🇩🇪 German</div><div class="mitup-key mitup-key--rich">🇧🇷 Portuguese</div><div class="mitup-key mitup-key--rich">🇮🇹 Italian</div></div><hr class="mitup-card__rule"/><span class="mitup-card__section"><strong>🌐 Timezone</strong></span> <span class="mitup-chip">✏️ Change</span><br/>Europe/Madrid<hr class="mitup-card__rule"/><span class="mitup-card__section"><strong>⏰ Notifications</strong></span> <span class="mitup-chip mitup-chip--success">Enabled</span><br/><br/><span class="mitup-card__section"><strong>Reminder</strong></span> <span class="mitup-chip mitup-chip--success">Enabled</span> <span class="mitup-chip">✏️ Change</span><br/>5 minutes before a meeting starts<br/><br/><span class="mitup-card__section"><strong>Deletion warning</strong></span> <span class="mitup-chip mitup-chip--success">Enabled</span><br/>7 days before a meeting is permanently deleted<br/><br/><span class="mitup-card__section"><strong>Deletion notice</strong></span> <span class="mitup-chip mitup-chip--success">Enabled</span><br/>Once a meeting is deleted<hr class="mitup-card__rule"/><span class="mitup-card__section"><strong>⌛ Timeout</strong></span> <span class="mitup-chip">✏️ Change</span><br/>A meeting without an end time stays active 5 minutes after it starts.<hr class="mitup-card__rule"/><span class="mitup-card__section"><strong>👥 Default Options</strong></span> <span class="mitup-chip">Open</span><br/>What every meeting you create starts with.<hr class="mitup-card__rule"/><span class="mitup-card__section"><strong>🛡️ Privacy</strong></span> <span class="mitup-chip">Open</span><br/>Your data, the policy, export and deletion.<hr class="mitup-card__rule"/><div class="mitup-bot-msg__row"><div class="mitup-key mitup-key--rich">≪ Main Menu</div></div></div></div></div>
        </div>
      </div>

    </div>
    <span class="mitup-two-up__caption">Your settings</span>
  </div>
</div>

## Getting around a meeting

The card is where you spend your time in Mitup, so that is where the rebuild went deepest.

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
      <div class="mitup-bot-msg"><div class="mitup-bot-msg__content"><div class="mitup-bot-msg__sender">mitupbot</div><div class="mitup-bot-msg__text"><span class="mitup-card__title" data-note="0">Weekend hike prep <span class="mitup-chip">✏️ Edit</span></span><hr class="mitup-card__rule"/><span class="mitup-card__section">🖼️ <strong>Images</strong></span><br/><span class="mitup-chip" data-note="1">+ Add images</span><hr class="mitup-card__rule"/><span class="mitup-card__section">📄 <strong>Description</strong></span><br/><span data-note="2">Bring water and sturdy boots.</span><br/><span class="mitup-chip">✏️ Edit</span> <span class="mitup-chip mitup-chip--danger">Clear</span><hr class="mitup-card__rule"/><span class="mitup-card__section">🕒 <strong>When</strong></span><br/><span data-note="3">Start time</span>: Tue, <span class="mitup-time">Sep 1</span>, <span class="mitup-time">18:00</span><br/><span class="mitup-chip">✏️ Edit</span> <span class="mitup-chip mitup-chip--danger">Remove</span><br/><br/>End time: Tue, <span class="mitup-time">Sep 1</span>, <span class="mitup-time">21:00</span><br/><span class="mitup-chip">✏️ Edit</span> <span class="mitup-chip mitup-chip--danger">Remove</span><br/><br/><span class="mitup-card__section">🗺️ <strong>Where</strong></span><br/><span data-note="4">Trailhead car park</span><br/><span class="mitup-chip">✏️ Edit</span> <span class="mitup-chip mitup-chip--danger">Remove</span><br/><br/>📍 <span class="mitup-chip">✏️ Edit</span> <span class="mitup-chip mitup-chip--danger">Remove</span><div class="mitup-card__map"></div><hr class="mitup-card__rule"/><span class="mitup-card__section">👥 <strong>Participants</strong></span> · 6 of 20<br/><span class="mitup-chip" data-note="5">Change limit</span> <span class="mitup-chip">Kick out</span><ul><li>Ana Marín <span class="mitup-chip mitup-chip--danger">❌ Leave</span></li><li>Diego</li><li>Sara</li><li>Tomás</li><li>Lucía</li></ul><span class="mitup-chip">😄 Invite</span><br/><br/>😴 Waiting list · 1<ul><li>Marta</li></ul><hr class="mitup-card__rule"/><div class="mitup-bot-msg__row"><div class="mitup-key mitup-key--rich mitup-key--primary" data-note="6">📨 Share</div></div><div class="mitup-bot-msg__row" style="--cols: 2"><div class="mitup-key mitup-key--rich">⚙️ Settings</div><div class="mitup-key mitup-key--rich mitup-key--danger">🗑️ Delete</div></div><div class="mitup-bot-msg__row"><div class="mitup-key mitup-key--rich">🔄 Refresh</div></div><div class="mitup-bot-msg__row"><div class="mitup-key mitup-key--rich">≪ Main Menu</div></div></div></div></div>
    </div>
  </div>
  <span class="mitup-annotation mitup-annotation--left" data-for="0" style="top: 98px;">
    <span class="mitup-annotation__label">Title</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--right" data-for="1" style="top: 157px;">
    <span class="mitup-annotation__label">Images</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--left" data-for="2" style="top: 250px;">
    <span class="mitup-annotation__label">Description</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--right" data-for="3" style="top: 344px;">
    <span class="mitup-annotation__label">When</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--left" data-for="4" style="top: 470px;">
    <span class="mitup-annotation__label">Where</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--right" data-for="5" style="top: 691px;">
    <span class="mitup-annotation__label">Guests</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--left" data-for="6" style="top: 945px;">
    <span class="mitup-annotation__label">Card buttons</span>
    <span class="mitup-annotation__line"></span>
  </span>
</div>

Each section carries the chips that change it: title, description, start, end, place. Tap one, answer the bot, and the card comes back with the new value in place.

Your own copy stays as you left it while people join and leave, and *🔄 Refresh*{.button-like} brings it up to date. The copies you shared into chats keep themselves current.
## For Mitup Hosts

Photos on a meeting are here, up to five, on every tier from Brewer up. One sits on its own, several show as a collage or a slideshow. They go above everything else on every copy of the card, so people see the trailhead before they read a word.

<!-- Photos in the showcase, all under the Pexels and Unsplash licences: hike-friends by Tanya Kash
     (pexels.com/photo/11724800), hike-ridge by Eric Sanman (pexels.com/photo/1365425), hike-boots by
     Bare Kind (unsplash.com/photos/NhD9ngc1XT0). -->
<div class="mitup-two-up">
  <div class="mitup-two-up__item">
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
          <div class="mitup-bot-msg"><div class="mitup-bot-msg__content"><div class="mitup-bot-msg__sender">mitupbot</div><div class="mitup-bot-msg__text"><span class="mitup-card__title">🖼️ Images</span>Send one or more photos to show them on the meeting card, in the order they should appear. A meeting holds up to 5 photos, and wide ones look best.<hr class="mitup-card__rule"/><div class="mitup-card__photos mitup-card__photos--slideshow"><div class="mitup-card__photo" style="background: url(../../assets/images/news/hike-friends.jpg) center / cover"></div></div><div class="mitup-card__dots"><span class="mitup-card__dot mitup-card__dot--on"></span><span class="mitup-card__dot"></span><span class="mitup-card__dot"></span></div><strong>Image 1</strong> <span class="mitup-chip">Replace</span> <span class="mitup-chip mitup-chip--danger">Remove</span><br/><span class="mitup-card__section"><strong>Image 2</strong></span> <span class="mitup-chip">Replace</span> <span class="mitup-chip mitup-chip--danger">Remove</span><br/><span class="mitup-card__section"><strong>Image 3</strong></span> <span class="mitup-chip">Replace</span> <span class="mitup-chip mitup-chip--danger">Remove</span><div class="mitup-bot-msg__row"><div class="mitup-key mitup-key--rich mitup-key--danger">Remove all</div></div><hr class="mitup-card__rule"/><span class="mitup-card__section"><strong>Layout</strong></span><br/>How several photos are drawn on the card: a collage shows them all at once, a slideshow shows one at a time.<div class="mitup-bot-msg__row" style="--cols: 2"><div class="mitup-key mitup-key--rich mitup-key--primary">Collage</div><div class="mitup-key mitup-key--rich">Slideshow</div></div><hr class="mitup-card__rule"/><div class="mitup-bot-msg__row"><div class="mitup-key mitup-key--rich">≪ Meeting</div></div></div></div></div>
        </div>
      </div>

    </div>
    <span class="mitup-two-up__caption">The Images screen</span>
  </div>
  <div class="mitup-two-up__item">
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
          <div class="mitup-bot-msg"><div class="mitup-bot-msg__content"><div class="mitup-bot-msg__sender">mitupbot</div><div class="mitup-bot-msg__text"><span class="mitup-card__title">Weekend hike prep</span><span class="mitup-card__footer">Created by: Ana Marín</span><br/><div class="mitup-card__photos mitup-card__photos--collage mitup-card__photos--3"><div class="mitup-card__photo" style="background: url(../../assets/images/news/hike-friends.jpg) center / cover"></div><div class="mitup-card__photo" style="background: url(../../assets/images/news/hike-ridge.jpg) center / cover"></div><div class="mitup-card__photo" style="background: url(../../assets/images/news/hike-boots.jpg) center / cover"></div></div><hr class="mitup-card__rule"/><span class="mitup-card__section">📄 <strong>Description</strong></span><br/>Bring water and sturdy boots.<hr class="mitup-card__rule"/><span class="mitup-card__section">🕒 <strong>When</strong></span><br/>Tue, <span class="mitup-time">Sep 1</span>, <span class="mitup-time">18:00</span> - <span class="mitup-time">21:00</span><br/><br/><span class="mitup-card__section">🗺️ <strong>Where</strong></span><br/>Trailhead car park<div class="mitup-card__map"></div><hr class="mitup-card__rule"/><span class="mitup-card__section">👥 <strong>Participants</strong></span> · 3 of 20<ul><li>Ana Marín</li><li>Diego</li><li>Sara</li></ul><hr class="mitup-card__rule"/><span class="mitup-card__footer">Built with <span class="mitup-chip">Mitup</span></span></div></div><div class="mitup-bot-msg__keyboard"><div class="mitup-bot-msg__row" style="--cols: 2"><div class="mitup-key mitup-key--success">✅ Join</div><div class="mitup-key mitup-key--danger">❌ Leave</div></div><div class="mitup-bot-msg__row"><div class="mitup-key">🔄 Refresh</div></div><div class="mitup-bot-msg__row"><div class="mitup-key">≪ Main Menu</div></div></div></div>
        </div>
      </div>

    </div>
    <span class="mitup-two-up__caption">A shared card with photos</span>
  </div>
</div>

This one goes to the Mitup Hosts, the people backing the bot on Patreon. It is what the busiest organizers kept asking for, and a meeting carrying several photos takes more work on our side every time one of its cards is drawn. [Limits and Host perks](../user-guide/limits.md) has what else a tier changes.

One catch before you share: a card with photos only goes into a chat where you are allowed to send photos, stickers and GIFs.

## Smaller things you will notice

* Each meeting carries its own date and time format, on every account.
* Bold, italic and links come through in titles and descriptions as you typed them.
* Weekday and month names follow each language's own conventions.
* *💾 Past meetings*{.button-like} closes with *🗑️ Delete all past meetings*{.button-like}, which empties the list after one confirmation.

## Notifications you control

Mitup sends three kinds of message about your own meetings: a reminder before one starts, a warning a week before an inactive one is deleted for good, and a notice once it is gone. Each has its own switch under *⏰ Notifications*{.button-like}, all three start on, and the section header turns the lot on or off at once.

When several of your meetings reach that deadline on the same day, the warning arrives as one message listing them. Your past meetings also carry the date each one is set to be deleted, right under the title.

A large batch of those falls in October. Mitup keeps an inactive meeting for 90 days, a year for Gamemasters and Commissioners, and plenty of meetings carried over from the old bot finished long outside that window. Owners get the usual week's notice, which points at *💾 Past meetings*{.button-like}, where anything worth keeping can be reactivated. [Meeting lifecycle](../user-guide/meeting_lifecycle.md) follows the whole path.

## What comes next

We have shipped a fair few updates since the new Mitup launched in July, and I expect that to carry on. What gets built next mostly comes from what people write in and ask for.

Bugs, requests and "why does it do this" all go to [support@mitup.social](mailto:support@mitup.social), and I read them. *♥ Collaborate*{.button-like} in the main menu shows the ways to pitch in, and *❓ Help*{.button-like} has every way to reach us.

Juanpe.
