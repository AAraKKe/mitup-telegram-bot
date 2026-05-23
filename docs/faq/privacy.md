---
icon: material/shield-lock-outline
---

# Privacy

Mitup was designed before "minimal data" was a marketing line. It just made sense not to collect what we didn't need. This page is the human-readable summary of exactly what data we keep, what we don't, and how to wipe it.

## TL;DR

| Data | Status |
|---|---|
| `user_id` | <span class="badge yes">stored</span> |
| `timezone` | <span class="badge yes">stored</span> |
| `meetings_you_own` | <span class="badge yes">stored</span> |
| `your_rsvps` | <span class="badge yes">stored</span> |
| `location_pin` | <span class="badge no">used once, discarded</span> |
| `group_chat_messages` | <span class="badge no">never read</span> |
| `analytics_sdks` | <span class="badge no">none installed</span> |
| `advertising_ids` | <span class="badge no">none. ever.</span> |

## Our principles

!!! tip "Collect the minimum"
    If the bot doesn't need it to do its job, we don't ask for it and we don't keep it.

!!! tip "No analytics SDKs"
    No Mixpanel, no Amplitude, no Sentry session-replay, no Google Analytics. The only telemetry we keep is our own server logs, retained for 14 days and used solely to debug.

!!! tip "No selling, no sharing"
    Your data isn't sold or shared with third parties. There is no third party in this stack at all besides Telegram itself.

!!! tip "You can wipe everything"
    Hard delete is a button. No grace period, no recovery. When you say go, it's gone.

## What we store

* **Telegram user ID**: the numeric identifier Telegram gives every account. It's how we know which meetings belong to whom.
* **Display name & language code**: pulled from your Telegram profile so we can address you and reply in your language.
* **Timezone**: set during onboarding. Used to schedule reminders in your local time.
* **Meetings you own**: the events you've created, with their title, time, options and RSVP list. Kept until you delete them.
* **Your RSVPs**: the meetings others invited you to and which ones you've joined.

## What we don't store

* Any message you send in a group or channel.
* The contents of conversations you have with anyone other than the bot.
* Phone numbers, email addresses, or any contact information you haven't explicitly given us.
* Any location data beyond the timezone derived from a one-time pin.

## How location handling works

When you set up Mitup, you can either type your city name or send a Telegram location pin. If you choose the pin, here's exactly what happens:

!!! note "The pin's lifecycle"
    1. Coordinates arrive over Telegram's API.
    2. We look up the timezone of those coordinates using a local geo database.
    3. We save **the timezone string** (e.g. `Europe/Dublin`).
    4. The coordinates are discarded before the request finishes. They never touch the database.

## How to delete your data

Type `/delete` in your chat with the bot. You'll be asked to confirm twice. Once confirmed, every record we have about you is removed, including:

* Your user record (Telegram ID, name, timezone, language)
* Every meeting you created. Your participants will see them disappear too.
* Every RSVP you've ever made on someone else's meeting

!!! warning "This is permanent"
    There is no soft-delete, no grace period, and no recovery. Once confirmed, your data is removed within seconds.

## Questions?

Reach us via the bot's **Help** button or our Service Desk. Privacy-specific concerns get answered by the same person who built the privacy stack, usually within a couple of days.
