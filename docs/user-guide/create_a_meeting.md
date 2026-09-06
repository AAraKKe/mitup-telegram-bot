---
description: "Create a meeting in Telegram: send a title, then fill in the card with photos, the date and time, the place and the guest limit, and share it."
icon: material/calendar-plus-outline
---

# Create a meeting

Tap *➕ New meeting*{.button-like} in the main menu and send the title. That's the only thing the bot asks for. The meeting is created with your [default options](settings.md#default-meeting-options) and its card comes up, ready to fill in and share.

Attach a date to the title message, using Telegram's own date and time formatting, and the meeting starts with its schedule set. Bold, italic and the rest of Telegram's text formatting come through as you typed them, in the title and in the description.

## The meeting card

Everything about a meeting lives on its card. Each section holds a value and, next to it, the chips that change it. Tap a chip, answer the bot, and the card comes back updated. Sections you leave empty are hidden from the people you share the meeting with, so a title alone is already a meeting you can share.

<!-- mock:owner_card -->
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
      <div class="mitup-bot-msg"><div class="mitup-bot-msg__content"><div class="mitup-bot-msg__sender">mitupbot</div><div class="mitup-bot-msg__text"><span class="mitup-card__title">Weekend hike prep <span class="mitup-chip">✏️ Edit</span></span><hr class="mitup-card__rule"/><span class="mitup-card__section">🖼️ <strong>Images</strong></span><br/><div class="mitup-card__photos mitup-card__photos--collage mitup-card__photos--3" data-note="0"><div class="mitup-card__photo"></div><div class="mitup-card__photo"></div><div class="mitup-card__photo"></div></div><span class="mitup-chip">✏️ Edit images</span><hr class="mitup-card__rule"/><span class="mitup-card__section">📄 <strong>Description</strong></span><br/>Bring water and sturdy boots.<br/><span class="mitup-chip">✏️ Edit</span> <span class="mitup-chip mitup-chip--danger">Remove</span><hr class="mitup-card__rule"/><span class="mitup-card__section">🕒 <strong>When</strong></span><br/><span data-note="1">Start time</span>: Tue, <span class="mitup-time">Sep 1</span>, <span class="mitup-time">18:00</span><br/><span class="mitup-chip">✏️ Edit</span> <span class="mitup-chip mitup-chip--danger">Remove</span><br/><br/>End time: Tue, <span class="mitup-time">Sep 1</span>, <span class="mitup-time">21:00</span><br/><span class="mitup-chip">✏️ Edit</span> <span class="mitup-chip mitup-chip--danger">Remove</span><br/><br/><span class="mitup-card__section">🗺️ <strong>Where</strong></span><br/>Trailhead car park<br/><span class="mitup-chip">✏️ Edit</span> <span class="mitup-chip mitup-chip--danger">Remove</span><br/><br/>📍 <span class="mitup-chip">✏️ Edit</span> <span class="mitup-chip mitup-chip--danger">Remove</span><div class="mitup-card__map"></div><hr class="mitup-card__rule"/><span class="mitup-card__section">👥 <strong>Participants</strong></span> · 3 of 20<br/><span class="mitup-chip" data-note="2">Change limit</span> <span class="mitup-chip">Kick out</span><ul><li>Ana Marín <span class="mitup-chip mitup-chip--danger">❌ Leave</span></li><li>Diego</li><li>Sara</li></ul><hr class="mitup-card__rule"/><div class="mitup-bot-msg__row"><div class="mitup-key mitup-key--rich mitup-key--primary" data-note="3">📨 Share</div></div><div class="mitup-bot-msg__row" style="--cols: 2"><div class="mitup-key mitup-key--rich">⚙️ Settings</div><div class="mitup-key mitup-key--rich mitup-key--danger">🗑️ Delete</div></div><div class="mitup-bot-msg__row"><div class="mitup-key mitup-key--rich">🔄 Refresh</div></div><div class="mitup-bot-msg__row"><div class="mitup-key mitup-key--rich">≪ Main Menu</div></div></div></div></div>
    </div>
  </div>
  <span class="mitup-annotation mitup-annotation--right" data-for="0" style="top: 288px;">
    <span class="mitup-annotation__label">Photos</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--left" data-for="1" style="top: 588px;">
    <span class="mitup-annotation__label">When</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--right" data-for="2" style="top: 935px;">
    <span class="mitup-annotation__label">Guests</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--left" data-for="3" style="top: 1067px;">
    <span class="mitup-annotation__label">Card buttons</span>
    <span class="mitup-annotation__line"></span>
  </span>
</div>
<!-- /mock -->

* **Title**: the heading, with its *✏️ Edit*{.button-like} chip.
* **Images**: up to five photos at the top of the card, a [Host](limits.md#photos-on-the-meeting-card) perk. See [Images](#images).
* **Description**: what to bring, the plan for the evening, a link to the venue.
* **When**: the start time and, once one exists, an end time. See [When](#when).
* **Where**: a place name, a map pin, or both. See [Where](#where).
* **Participants**: who is coming, with the waiting list under it. *Change limit*{.button-like} caps the list and *Kick out*{.button-like} removes someone after you confirm. Creating a meeting doesn't put you on its list: tap *✅ Join*{.button-like} on the card if you're going, and your row then carries *❌ Leave*{.button-like}.

Under the card, *📨 Share*{.button-like} posts the meeting into a chat, *⚙️ Settings*{.button-like} opens the [meeting settings](meeting_settings.md), *🗑️ Delete*{.button-like} removes the meeting after you confirm, and *🔄 Refresh*{.button-like} redraws the card. Your own card doesn't update by itself as people join and leave; the copies shared into chats do.

## Images

Hosts can put up to five photos on a meeting, on [every Host tier](limits.md#photos-on-the-meeting-card). They show at the top of every copy of the card: one on its own, several as a collage or a slideshow.

Tap *+ Add images*{.button-like} on the card, or *✏️ Edit images*{.button-like} once it has some, and send a photo or a whole album. On the Images screen each photo can be replaced or removed, and with several you pick collage or slideshow.

!!! note "Sharing a meeting with photos"

    A card with photos can only be posted into a chat where you are allowed to send photos,
    stickers and GIFs. Group owners and admins always are; for everyone else the group's
    permissions decide. See [sharing and joining](sharing_and_joining.md#group-permissions).

If your Host support ends, the photos stay on the card and you can still remove them. Adding or replacing needs an active tier.

## When

Tap *+ Set date & time*{.button-like}, pick the day on the calendar and set the time. Once a start exists, *+ Set end time*{.button-like} adds an end, which has to be after the start and [within a week of it](limits.md#how-long-one-meeting-can-last). Each row keeps its own *✏️ Edit*{.button-like} and *Remove*{.button-like} chips, and removing the start removes the end with it.

<!-- mock:when_editor -->
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
      <div class="mitup-bot-msg"><div class="mitup-bot-msg__content"><div class="mitup-bot-msg__sender">mitupbot</div><div class="mitup-bot-msg__text">Select the date and time of your meeting from the calendar.<br/><br/><em>Tip: you can also send a message using <a href="https://telegram.org/blog/member-tags-disable-sharing-and-more#time-and-date-formatting">Telegram's date &amp; time formatting</a> to set the date and time at once.</em><table class="mitup-card__table"><tr><th data-note="0">Mon</th><th>Tue</th><th>Wed</th><th>Thu</th><th>Fri</th><th>Sat</th><th>Sun</th></tr><tr><td></td><td align="center"><span class="mitup-chip mitup-chip--success">1</span></td><td align="center"><span class="mitup-chip">2</span></td><td align="center"><span class="mitup-chip">3</span></td><td align="center"><span class="mitup-chip">4</span></td><td align="center"><span class="mitup-chip">5</span></td><td align="center"><span class="mitup-chip">6</span></td></tr><tr><td align="center"><span class="mitup-chip">7</span></td><td align="center"><span class="mitup-chip">8</span></td><td align="center"><span class="mitup-chip">9</span></td><td align="center"><span class="mitup-chip">10</span></td><td align="center"><span class="mitup-chip">11</span></td><td align="center"><span class="mitup-chip">12</span></td><td align="center"><span class="mitup-chip">13</span></td></tr><tr><td align="center"><span class="mitup-chip">14</span></td><td align="center"><span class="mitup-chip">15</span></td><td align="center"><span class="mitup-chip">16</span></td><td align="center"><span class="mitup-chip">17</span></td><td align="center"><span class="mitup-chip">18</span></td><td align="center"><span class="mitup-chip">19</span></td><td align="center"><span class="mitup-chip">20</span></td></tr><tr><td align="center"><span class="mitup-chip">21</span></td><td align="center"><span class="mitup-chip">22</span></td><td align="center"><span class="mitup-chip">23</span></td><td align="center"><span class="mitup-chip">24</span></td><td align="center"><span class="mitup-chip">25</span></td><td align="center"><span class="mitup-chip">26</span></td><td align="center"><span class="mitup-chip">27</span></td></tr><tr><td align="center"><span class="mitup-chip">28</span></td><td align="center"><span class="mitup-chip">29</span></td><td align="center"><span class="mitup-chip">30</span></td><td></td><td></td><td></td><td></td></tr></table><div class="mitup-bot-msg__row" style="--cols: 2"><div class="mitup-key mitup-key--rich mitup-key--disabled" data-note="1">September</div><div class="mitup-key mitup-key--rich">≫</div></div><div class="mitup-bot-msg__row" style="--cols: 2"><div class="mitup-key mitup-key--rich mitup-key--disabled">2026</div><div class="mitup-key mitup-key--rich">≫</div></div><hr class="mitup-card__rule"/>🕒 <span data-note="2">Current time</span>: <strong>18:00</strong> <span class="mitup-chip">✏️ Edit</span><hr class="mitup-card__rule"/><div class="mitup-bot-msg__row"><div class="mitup-key mitup-key--rich mitup-key--danger">Remove date & time</div></div><div class="mitup-bot-msg__row"><div class="mitup-key mitup-key--rich">≪ Meeting</div></div></div></div></div>
    </div>
  </div>
  <span class="mitup-annotation mitup-annotation--left" data-for="0" style="top: 206px;">
    <span class="mitup-annotation__label">Pick a day</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--right" data-for="1" style="top: 352px;">
    <span class="mitup-annotation__label">Month and year</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--left" data-for="2" style="top: 448px;">
    <span class="mitup-annotation__label">The time</span>
    <span class="mitup-annotation__line"></span>
  </span>
</div>
<!-- /mock -->

How far ahead you can schedule depends on [your account](limits.md#scheduling-ahead). A meeting with a start and no end goes inactive shortly after it starts; the [meeting lifecycle](meeting_lifecycle.md) page has the details. Freezing the guest list while the meeting runs is a setting, [lock on start](meeting_settings.md#lock-on-start).

## Where

A place has a name, a map pin, or both. *+ Set name*{.button-like} takes any text: a bar, a park entrance, a video-call link. *+ Set map pin*{.button-like} takes a location sent from your phone, through the attachment icon and Location, and it can be any spot on the map, not only where you are. With a pin set, the map is drawn on the card and opens the spot when tapped.
