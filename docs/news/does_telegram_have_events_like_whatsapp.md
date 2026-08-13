---
description: "Telegram has no built-in events feature and does not need one: Mitup has organized events in Telegram groups since 2015, nine years before WhatsApp."
icon: material/newspaper-variant-outline
hide:
    - toc
---

# Does Telegram have events like WhatsApp?

<span class="news-meta">14 August 2026</span>

I have not had a WhatsApp account since 2010, so WhatsApp news reaches me by word of mouth. This week a friend mentioned that WhatsApp has native events in group chats now. I looked them up today, and I cannot quite believe how familiar they look.

So, the question in the title. Short answer: Telegram has no built-in events feature, and it is not missing one. WhatsApp added Events to group chats in 2024. Telegram groups have been organizing this way **since 2015** through [Mitup](https://t.me/mitupbot?start=src_web), an event bot that never joins your group. Same job, nine years earlier, and with a few tricks WhatsApp still does not have.

## The WhatsApp Events timeline

Credit where it is due: WhatsApp Events is well made, and it has been improving steadily. The timeline so far:

* **May 2024**: Events arrive in WhatsApp Communities ([Meta newsroom](https://about.fb.com/news/2024/05/events-in-whatsapp-communities/)).
* **August 2024**: rollout to all group chats ([MacRumors](https://www.macrumors.com/2024/08/05/whatsapp-rolls-out-events-group-chats/)).
* **2025**: events in 1:1 chats, answering with a maybe, bringing a plus one, end times, pinning ([WhatsApp blog](https://blog.whatsapp.com/new-feature-roundup-updates-to-group-chats-events-calls-channels-and-more)).
* **January 2026**: event reminders ([Meta newsroom](https://about.fb.com/news/2026/01/whatsapp-group-chats-member-tags-text-stickers-event-reminders/)). A day-one feature in Mitup, eleven years earlier.
* **August 2026**: another round of group-planning upgrades, polls with deadlines and quick side-groups for organizing ([Meta newsroom](https://about.fb.com/news/2026/08/were-upgrading-your-whatsapp-group-chats/)).

If that list looks familiar, it is because every item on it describes a Tuesday on Mitup: create a meeting, share it to the group, join with a tap, get a reminder before it starts. That loop, waiting list and timezone-aware reminders included, has been live in Telegram **since 2015**. We are not saying a small Spanish Telegram bot inspired a feature now used by two billion people. We are just saying that if someone had wanted a head start, the reference implementation was sitting right there the whole time.

Even the newest idea on that list has a Mitup ancestor. Those quick side-groups for planning from the August update? The old Mitup had a meeting chat: anyone in the event could send messages through the bot that only the other participants could read. We removed it during the rebuild ([the story behind it](welcome_to_the_new_mitup.md)) to build it back properly if people want it. If that is the feature you would miss most, say so at [hello@mitup.social](mailto:hello@mitup.social); enough asks and it jumps the queue.

## What a decade of head start buys

A decade of doing one job also buys you the parts WhatsApp has not gotten to:

* **Your event is not locked to one chat.** A WhatsApp event lives inside the chat it was created in. A Mitup card travels: share it to a group, a channel, or a friend's DM, and every copy stays in sync. Anyone who receives it can join. Make the meeting public and anyone who receives the card can share it onward too, which is how a public event reaches people you have never met.
* **No bot in your group.** Mitup works through Telegram's inline mode, so it never joins the chat. It works even in groups where admins do not add bots.
* **A waiting list when it fills up, since 2015.** Cap the spots and the overflow queues in order. When someone drops out, the next person is promoted and notified automatically. Perfect for board-game nights where the table only seats so many and everyone still wants a shot if a spot frees up. Nothing on the WhatsApp timeline matches it yet.
* **Reminders in each person's timezone, since 2015.** A meeting shared across three countries reminds each person at the right local hour. WhatsApp events got reminders in January 2026.
* **Guests without an account, since 2018.** Someone asked you in person? Add them to the list by name. They do not need Telegram at all. WhatsApp added plus ones in 2025; Mitup guests have had names for seven years.
* **Open source and free.** Ten years online, [MIT-licensed](https://gitlab.com/meetupbot/mitup-telegram-bot), translated into six languages by its community.

And one difference that is not a feature but a principle: Mitup is not in the data business. No ads, nothing sold about you, ever. If you ever want to leave, you can export or delete everything the bot knows about you, straight from Settings. The [privacy page](../faq/privacy.md) spells it all out.

If your people plan in WhatsApp, Events is right there and it is good. If your people are on Telegram and want the equivalent of WhatsApp Events, the ten-year head start is yours to use: open [@mitupbot](https://t.me/mitupbot?start=src_web) and share your first card. New here? The [getting started guide](../user-guide/getting_started.md) walks you from first tap to first shared card.
