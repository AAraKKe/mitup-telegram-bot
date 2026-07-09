---
icon: material/shield-lock-outline
---

# Privacy

This page describes exactly what data Mitup stores, what it does with that data, and what rights you have over it.

## What we keep, and why

Each section is one kind of relationship the bot has with your data: from "we hold this so the product works" all the way down to "we never see it".

<div class="tldr-stack">
  <section class="tldr-card tldr--stored">
    <header class="tldr-card__head">
      <div>
        <h3>Who you are on Telegram</h3>
        <p>Telegram gives the bot these when you start a chat. They are whatever you set in your Telegram profile, real name or not, and we have no way to verify which. We keep them to recognise you between sessions and to show your name to people you share meetings with.</p>
      </div>
      <span class="tldr-pill"><span class="dot"></span>Stored</span>
    </header>
    <div class="tldr-rows">
      <div class="tldr-row">
        <div class="tldr-row__label">Telegram ID</div>
        <div class="tldr-row__why">Tells the bot which records belong to you.</div>
      </div>
      <div class="tldr-row">
        <div class="tldr-row__label">First name</div>
        <div class="tldr-row__why">Greets you, and shows the name you set in your Telegram profile to others.</div>
      </div>
      <div class="tldr-row">
        <div class="tldr-row__label">Last name</div>
        <div class="tldr-row__why">Shows the name you set in your Telegram profile to others. Optional.</div>
      </div>
      <div class="tldr-row">
        <div class="tldr-row__label">Username</div>
        <div class="tldr-row__why">Shows the handle you set in your Telegram profile to others. Optional.</div>
      </div>
    </div>
  </section>

  <section class="tldr-card tldr--stored">
    <header class="tldr-card__head">
      <div>
        <h3>How you want the bot to behave</h3>
        <p>Preferences you set or that we infer once, so the bot can answer in your language and at the right hour.</p>
      </div>
      <span class="tldr-pill"><span class="dot"></span>Stored</span>
    </header>
    <div class="tldr-rows">
      <div class="tldr-row">
        <div class="tldr-row__label">Language</div>
        <div class="tldr-row__why">Replies in your preferred language (defaults from Telegram, changeable in Settings).</div>
      </div>
      <div class="tldr-row">
        <div class="tldr-row__label">Timezone</div>
        <div class="tldr-row__why">Shows meeting times in your local zone, and sends reminders at the right hour.</div>
      </div>
      <div class="tldr-row">
        <div class="tldr-row__label">Meeting defaults</div>
        <div class="tldr-row__why">Your default settings for new meetings.</div>
      </div>
      <div class="tldr-row">
        <div class="tldr-row__label">Notification settings</div>
        <div class="tldr-row__why">Reminder timing, and whether notifications are on or off.</div>
      </div>
    </div>
  </section>

  <section class="tldr-card tldr--stored">
    <header class="tldr-card__head">
      <div>
        <h3>Your meetings and who is in them</h3>
        <p>The actual product data. Without this the bot is nothing more than a name in a chat list.</p>
      </div>
      <span class="tldr-pill"><span class="dot"></span>Stored</span>
    </header>
    <div class="tldr-rows">
      <div class="tldr-row">
        <div class="tldr-row__label">Meetings you've created</div>
        <div class="tldr-row__why">The meetings you've made, with their title, time, options, and RSVP list.</div>
      </div>
      <div class="tldr-row">
        <div class="tldr-row__label">Meetings you've joined</div>
        <div class="tldr-row__why">The meetings others invited you to and whether you accepted, declined, or are on the waiting list.</div>
      </div>
      <div class="tldr-row">
        <div class="tldr-row__label">Meeting locations</div>
        <div class="tldr-row__why">Coordinates attached to a meeting by the owner so guests can navigate there.</div>
      </div>
      <div class="tldr-row">
        <div class="tldr-row__label">Bot's own messages<span class="tldr-row__flag"><span class="tldr-pill"><span class="dot"></span>Temporary</span></span></div>
        <div class="tldr-row__why">Chat and message IDs for bot-sent messages, never the message text itself, so the bot can edit or delete them when something changes.</div>
      </div>
      <div class="tldr-row">
        <div class="tldr-row__label">Who invited you</div>
        <div class="tldr-row__why">Record of which participant invited you to a meeting.</div>
      </div>
    </div>
  </section>

  <section class="tldr-card tldr--stored">
    <header class="tldr-card__head">
      <div>
        <h3>If you link Patreon</h3>
        <p>Only exists when you link your Patreon account through the bot's Collaborate screen, to back Mitup as a Host. Unlinking removes it immediately.</p>
      </div>
      <span class="tldr-pill"><span class="dot"></span>Stored</span>
    </header>
    <div class="tldr-rows">
      <div class="tldr-row">
        <div class="tldr-row__label">Patreon account ID</div>
        <div class="tldr-row__why">Matches your Telegram account to your Patreon membership. Just the numeric ID, nothing else from your Patreon profile.</div>
      </div>
      <div class="tldr-row">
        <div class="tldr-row__label">Host tier</div>
        <div class="tldr-row__why">Which supporter tier you're on, so your badge and limits are right.</div>
      </div>
      <div class="tldr-row">
        <div class="tldr-row__label">Support end date</div>
        <div class="tldr-row__why">When your current membership period runs out, so your perks switch off at the right time.</div>
      </div>
    </div>
  </section>

  <section class="tldr-card tldr--transient">
    <header class="tldr-card__head">
      <div>
        <h3>Touched briefly, then gone</h3>
        <p>Sent to the bot for a single purpose, then discarded inside the same request. Never written to disk.</p>
      </div>
      <span class="tldr-pill"><span class="dot"></span>Used once, discarded</span>
    </header>
    <div class="tldr-rows">
      <div class="tldr-row">
        <div class="tldr-row__label">Location pin<span class="tldr-row__sub">timezone setup only</span></div>
        <div class="tldr-row__why">Sent to Google for a timezone lookup, then thrown away. Never stored.</div>
      </div>
      <div class="tldr-row">
        <div class="tldr-row__label">Patreon sign-in tokens<span class="tldr-row__sub">linking only</span></div>
        <div class="tldr-row__why">Used once during linking to confirm which Patreon account is yours, then discarded. Never stored.</div>
      </div>
    </div>
  </section>

  <section class="tldr-card tldr--never">
    <header class="tldr-card__head">
      <div>
        <h3>Never collected</h3>
        <p>Things we explicitly don't ask for, don't receive, and don't run.</p>
      </div>
      <span class="tldr-pill"><span class="dot"></span>Never collected</span>
    </header>
    <div class="tldr-rows">
      <div class="tldr-row">
        <div class="tldr-row__label">Group chat content</div>
        <div class="tldr-row__why">The bot runs with Telegram's Privacy Mode on. It only sees commands and direct replies.</div>
      </div>
      <div class="tldr-row">
        <div class="tldr-row__label">Analytics SDKs</div>
        <div class="tldr-row__why">No Mixpanel, Amplitude, Sentry session replay, or Google Analytics.</div>
      </div>
      <div class="tldr-row">
        <div class="tldr-row__label">Advertising IDs</div>
        <div class="tldr-row__why">Never collected.</div>
      </div>
    </div>
  </section>
</div>


## Other things we don't store

* Private conversations you have with anyone other than the bot.
* Phone numbers, email addresses, or contact information you haven't explicitly given us.
* Video, audio, or photo metadata.

## How location handling works

When you set up your timezone, you can type a city name or send a Telegram location pin. We use the Google Maps APIs to look up your timezone from either the text or the location pin's coordinates (its latitude and longitude). Here's exactly what happens in each case:

!!! note "Setting timezone from a pin"
    1. Coordinates arrive over Telegram's API.
    2. We send those coordinates to the Google Maps Time Zone API.
    3. Google returns an IANA timezone string (e.g. `Europe/Dublin`).
    4. We save the timezone string.
    5. The coordinates are discarded. They are never stored, persisted, or tied back to you.

!!! note "Setting timezone from a city"
    1. You type the name of a city.
    2. We send that text to the Google Maps Geocoding API.
    3. Google returns coordinates for the city.
    4. We send those coordinates to the Google Maps Time Zone API.
    5. Google returns an IANA timezone string.
    6. We save the timezone string.
    7. The coordinates and the city name are discarded. They are never stored or tied back to you.

In both cases, the Google Maps lookups are stateless queries that cannot be tied back to you by us. We send only the city name or coordinates needed for the lookup. We never send your Telegram ID, name, or any other profile information.

## How Patreon linking works

Backing Mitup on Patreon is optional, and so is telling the bot about it. If you want your Host badge and higher limits, you link your Patreon account through *♥ Collaborate*{.button-like} in the main menu:

1. The bot sends you to Patreon, where you approve the connection on Patreon's own page.
2. Patreon hands the bot a one-time sign-in token. The bot uses it once, to ask Patreon "which account is this, and are they a member of the Mitup campaign?", then discards it.
3. The bot saves your numeric Patreon account ID, your tier, and when your current membership period ends. That is the whole record.

We never see your payment details, your card, your address, or your Patreon email. Payment stays entirely between you and Patreon. Unlinking from the same Collaborate screen deletes the link record immediately and returns your account to the free limits.

## Why we're allowed to process this

For the GDPR-minded, the legal bases are the boring ones:

* **Performing the service you asked for** covers everything in the tables above: your Telegram identity, your preferences, your meetings and RSVPs, and the Patreon link you set up yourself.
* **Legitimate interest** covers operational logs and keeping the bot safe and running.
* Nothing is processed for advertising, profiling, or sale. There is no basis to look for because the processing doesn't exist.

## How long we keep your data

**Your user record** persists while you're an active Mitup user. If you were once a member and then block the bot (or if a message we send to you fails), we set a flag on your account. A cleanup job runs periodically and deletes all flagged users, which cascades to your settings, owned meetings, and RSVPs.

If you joined a meeting via someone else's group message without ever opening the bot directly, your record exists only for the lifetime of those meetings and is removed when they end.

**Meetings you're invited to** (RSVPs): when the meeting is deleted or you leave it, your RSVP is removed.

**Meetings you own:** when a meeting becomes inactive, how long it's kept afterward, and when it's permanently deleted are covered on the [Meeting lifecycle](../user-guide/meeting_lifecycle.md#how-long-an-inactive-meeting-is-kept) page.

**Your Patreon link** exists only while you keep it. Unlink from the Collaborate screen and the record is deleted on the spot. Deleting your user record removes it too.

## Your rights and how to exercise them

**Access and export.** Tap *🛡️ Privacy*{.button-like} under *⚙️ Settings*{.button-like}, then *📦 Export my data*{.button-like}. The bot replies with a JSON file containing everything Mitup stores about you: your user record, your settings, every meeting you own, every meeting you've joined, and your Patreon link if you have one. Other people in your meetings appear by their display name only. If you've blocked the bot, unblock it and the button works again. If something else is in the way, email `privacy@mitup.social` and we'll figure it out together.

**Rectification.** Edit your display name, language, or timezone directly in the bot's settings menu. For other corrections, email us.

**Erasure.** Tap *🛡️ Privacy*{.button-like} under *⚙️ Settings*{.button-like}, then *🗑️ Delete my data*{.button-like}, and confirm twice. Once confirmed, your account is marked for deletion and stops working right away, and within a day your user record, every meeting you own, every RSVP you've made, and your Patreon link are permanently removed. You get one final message confirming it's done. If you ever want to use Mitup again, send the `/start` command in your chat with the bot and it sets you up from scratch, with no memory of your old account.

**Portability.** The export is plain JSON, formatted for import into another system if you wish.

**Right to object.** If you believe Mitup is processing your data unfairly, email us.

**Complaints.** If you believe your rights have been infringed, you can also lodge a complaint with your local data protection supervisory authority. In the EU, that's the authority of the country you live in.

**Who the controller is.** Mitup is maintained by individuals, not a registered company. The maintainers decide what data is collected and why, which makes them the data controller in GDPR terms. If you have concerns the maintainers don't resolve, you can raise them via email or by opening an issue in the [GitLab repository](https://gitlab.com/meetupbot/mitup-telegram-bot/-/issues).

## Security

**In transit.** All communication between your client and Mitup's servers uses TLS encryption.

**At rest.** Mitup's database is a managed PostgreSQL instance on AWS, in the Ireland region (eu-west-1), inside the EU. Storage is encrypted at rest with an AWS-managed key. Database backups are retained for 7 days. A final snapshot is taken if the instance is ever destroyed. There is no cross-region replication.

**Secrets management.** API keys and database credentials live in AWS Systems Manager Parameter Store (SecureString), not in code. Access is gated by IAM.

**What we don't do.** We don't undergo regular third-party security audits or hold formal certifications like SOC 2. We rely on AWS's infrastructure and standard operational hygiene.

## Third parties

Mitup shares your data only with:

* **Telegram.** The bot sends messages to your account and receives messages from you via Telegram's infrastructure.
* **Google (Google Maps Platform).** When you set up your timezone or attach a location to a meeting, we use the Google Maps Time Zone API to resolve coordinates into an IANA timezone string. When you type an address rather than sending a pin, we also use the Google Maps Geocoding API to resolve that address into coordinates. We send only the address text or the coordinates themselves. We never send your Telegram ID, your name, or any other piece of your profile. Google receives a one-off lookup query that cannot be tied back to you by us.
* **Patreon.** Only if you link your Patreon account. The bot asks Patreon for your account ID and your membership status in the Mitup campaign, nothing more. Your pledge, payment method, and billing details live on Patreon and never reach the bot. See [how Patreon linking works](#how-patreon-linking-works).
* **AWS.** Your data lives on managed AWS services in the EU (ECS where the bot runs, RDS for the Postgres database, Lambda for migrations, SSM Parameter Store for secrets, CloudWatch for operational logs, S3 and CloudFront for this documentation site). AWS processes that data on our behalf as an infrastructure provider, under AWS's standard GDPR data processing addendum. We decide what is stored and how it's used.

No analytics firms, ad networks, or third-party tracking services have access to your data.

## Breaches

If user data is exposed due to a breach in Mitup's infrastructure or a compromise of the bot's credentials, we will:

1. Notify affected users via Telegram message as soon as we're aware.
2. Post a summary on this page.
3. Follow any applicable legal requirements for breach notification (e.g. GDPR Article 33).

## If Mitup shuts down

If the project ceases operations, all user data in the database will be deleted. We'll announce the shutdown via the bot's main menu message and on this page at least 30 days in advance.

## Changes to this policy

Material changes to this policy will be announced via a notice in the bot's main menu and on this page. The revision date below shows the last update.

---

**Last revised:** July 9, 2026

---

## Questions or concerns?

Email `privacy@mitup.social` or open an issue on [GitLab](https://gitlab.com/meetupbot/mitup-telegram-bot/-/issues).

For more about who maintains Mitup, visit the [About us](about.md) page.
