---
icon: material/calendar-clock-outline
---

# Meeting lifecycle

Every meeting has two lives. While it's active, people can join, leave, and see it in the cards you shared into your chats. Once it finishes it becomes inactive: read-only, kept under *💾 Your past meetings*{.button-like} until you reactivate it or delete it. That button is where your inactive meetings live.

This page covers when a meeting switches from active to inactive, what happens to it then, and how to bring it back or remove it for good.

<div class="mlc">
  <div class="mlc__row">
    <div class="mlc__stage mlc__stage--active">
      <span class="mlc__badge">Active</span>
      <span class="mlc__desc">People can join, leave, and see it in your shared cards.</span>
    </div>
    <div class="mlc__arrow">
      <span class="mlc__arrow-glyph">&rarr;</span>
      <span class="mlc__arrow-label">a few minutes after it finishes</span>
    </div>
    <div class="mlc__stage mlc__stage--inactive">
      <span class="mlc__badge">Inactive</span>
      <span class="mlc__desc">Kept in your Past meetings for about six months.</span>
    </div>
    <div class="mlc__arrow">
      <span class="mlc__arrow-glyph">&rarr;</span>
      <span class="mlc__arrow-label">if you never bring it back</span>
    </div>
    <div class="mlc__stage mlc__stage--deleted">
      <span class="mlc__badge">Deleted</span>
      <span class="mlc__desc">Removed permanently.</span>
    </div>
  </div>
  <div class="mlc__notes">
    <div class="mlc__note mlc__note--loop">Reactivate takes an inactive meeting back to Active and resets the six-month countdown.</div>
    <div class="mlc__note mlc__note--flag">About a week before deletion, you get a one-time heads-up with a button to reactivate.</div>
  </div>
</div>

<style>
.mlc {
  border: 1px solid var(--mitup-line);
  border-radius: 14px;
  background: var(--mitup-paper);
  padding: 1.5rem;
  margin: 1.5rem 0;
}
.mlc__row {
  display: flex;
  flex-wrap: nowrap;
  align-items: stretch;
  justify-content: center;
  gap: 0.6rem;
}
.mlc__stage {
  flex: 1 1 0;
  min-width: 0;
  max-width: 220px;
  background: #ffffff;
  border: 1px solid var(--mitup-line);
  border-top: 4px solid var(--mitup-ink-3);
  border-radius: 12px;
  padding: 0.9rem 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}
.mlc__stage--active   { border-top-color: var(--mitup-green); }
.mlc__stage--inactive { border-top-color: var(--mitup-blue); }
.mlc__stage--deleted  { border-top-color: var(--mitup-ink-3); }
.mlc__badge {
  font-weight: 800;
  color: var(--mitup-ink);
  font-size: 1rem;
}
.mlc__stage--active .mlc__badge   { color: var(--mitup-green-deep); }
.mlc__stage--inactive .mlc__badge { color: var(--mitup-blue-deep); }
.mlc__desc {
  color: var(--mitup-ink-2);
  font-size: 0.8rem;
  line-height: 1.4;
}
.mlc__arrow {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.2rem;
  flex: 0 0 72px;
  min-width: 0;
}
.mlc__arrow-glyph {
  color: var(--mitup-ink-3);
  font-size: 1.5rem;
  line-height: 1;
}
.mlc__arrow-label {
  color: var(--mitup-ink-3);
  font-size: 0.7rem;
  text-align: center;
  line-height: 1.3;
}
.mlc__notes {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
  margin-top: 1.25rem;
}
.mlc__note {
  font-size: 0.82rem;
  line-height: 1.4;
  color: var(--mitup-ink-2);
  padding: 0.6rem 0.85rem;
  border-radius: 10px;
  border: 1px solid var(--mitup-line);
}
.mlc__note--loop {
  border-left: 4px solid var(--mitup-green);
  background: rgba(79, 178, 134, 0.08);
}
.mlc__note--flag {
  border-left: 4px solid var(--mitup-yellow);
  background: rgba(255, 200, 80, 0.10);
}
@media (max-width: 620px) {
  .mlc__row { flex-direction: column; }
  .mlc__stage { max-width: none; width: 100%; }
  .mlc__arrow { flex-direction: row; }
  .mlc__arrow-glyph { transform: rotate(90deg); }
}
</style>

## When a meeting becomes inactive

You don't mark a meeting as finished by hand. Mitup does it for you a short while after the meeting's clock runs out. That delay is your *⌛ Timeout*{.button-like} setting, five minutes by default, which you can change under [Your settings](settings.md#timeout). Which clock Mitup watches depends on the times you set:

* **Start and end time set.** It becomes inactive a few minutes after the end time.
* **Start time only.** It becomes inactive a few minutes after the start time. There's no separate duration that keeps it open.
* **No date at all.** It stays active so you can still add a date later. If you never do, Mitup tidies it up a year after you created it.

!!! warning "A meeting with no end time closes right after it starts"

    If you set only a start time, the meeting doesn't stay open for the whole evening. It
    becomes inactive a few minutes after the start. A board game night that starts at 20:00
    with no end time is inactive by around 20:05.

    To keep it joinable through the meeting, set an end time. Mitup then waits until a few
    minutes after the end instead.

## What happens when it becomes inactive

Becoming inactive is tidy, not destructive. Here's what changes:

* The cards you shared into chats pick up a "finished" note and lose their buttons, so no one can still tap Join.
* The meeting moves into your *💾 Your past meetings*{.button-like} list, where you can still open it and see everything, participant list included.
* Its date and time stay exactly as you set them. Nothing is cleared.
* The only people removed are guests you invited who never signed up for the bot themselves. Their temporary entry is cleaned up, and everyone with a Mitup account stays on the list.

## Reminders around the start

Reminders are for the people who joined, not for you as the owner. They go out only to participants who have notifications on, and only when the meeting has a start time.

* A reminder lands shortly before the start. How far ahead is each person's own choice under *⚙️ Settings*{.button-like} then *⏰ Notifications*{.button-like}, five minutes by default, and anyone can turn it off.
* A second message lands when the start time arrives.

Both land in each participant's own timezone, so a meeting at 19:00 in Madrid reminds someone in Lisbon at 18:00 their time.

## Reactivating an inactive meeting

A meeting isn't gone once it's inactive. Open *💾 Your past meetings*{.button-like} from the main menu, pick the one you want, and you'll see it with two choices.

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
            This meeting is no longer active. Reactivate it to share it again, or delete it permanently.<br/><br/>
            <strong>Weekend Hike Prep</strong> (Created by: Ana Marín)<br/><br/>
            --- 📄 Bring water and sturdy boots.<br/>
            --- ▶️ Starts: Sat, 12 Jul, 09:00<br/>
            --- ⏹️ Ends: Sat, 12 Jul, 13:00<br/>
            --- 🗺️ Trailhead car park 📍<br/>
            --- 👥 3 participants (No limit)<br/>
            &nbsp;&nbsp;Ana Marín<br/>
            &nbsp;&nbsp;Diego<br/>
            &nbsp;&nbsp;Sara
          </div>
        </div>
        <div class="mitup-bot-msg__keyboard">
          <div class="mitup-bot-msg__row mitup-bot-msg__row--2">
            <div class="mitup-key">Reactivate meeting</div>
            <div class="mitup-key">🗑️ Delete</div>
          </div>
          <div class="mitup-bot-msg__row">
            <div class="mitup-key">≪ 💾 Your past meetings</div>
          </div>
        </div>
      </div>
    </div>
  </div>
  <span class="mitup-annotation mitup-annotation--left" style="top: 101px;">
    <span class="mitup-annotation__label">Finished</span>
    <span class="mitup-annotation__line"></span>
  </span>
  <span class="mitup-annotation mitup-annotation--right" style="top: 333px;">
    <span class="mitup-annotation__label">Bring it back</span>
    <span class="mitup-annotation__line"></span>
  </span>
</div>

Tap *Reactivate meeting*{.button-like} and it's active again. You can edit it, share it, and take RSVPs as before. Reactivating also resets the deletion countdown described below, so a meeting you keep bringing back never gets removed.

## Deleting a meeting

You can delete an active meeting from its card with *🗑️ Delete*{.button-like}, or an inactive one from the screen above. Either way Mitup asks you to confirm with *✅ Confirm*{.button-like} before anything happens.

!!! warning "Deletion is permanent"

    Deleting a meeting removes it right away, with no grace period and no undo. The meeting,
    its participant list, and the invited-only guests attached to it are gone for good. If you
    might want it back, reactivate it instead of deleting it.

## How long an inactive meeting is kept

An inactive meeting doesn't sit around forever. Mitup keeps it for about six months, then deletes it permanently along with its participant list.

About a week before that happens, you get a one-time heads-up that the meeting is about to be removed. It comes with a *Reactivate meeting*{.button-like} button, so bringing it back is one tap away. Reactivating at any point resets the countdown, so the only meetings removed automatically are the ones you leave untouched for six months.

Deleting a meeting yourself skips all of this. It's removed right away, as described above.
