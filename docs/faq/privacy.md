---
description: "Exactly what data the Mitup Telegram bot stores, how long it keeps it, who it shares it with, and how to export or delete your account."
icon: material/shield-lock-outline
---

# Privacy

This page describes exactly what data Mitup stores, what it does with that data, and what rights you have over it.

## What we keep, and why

Each section is one kind of relationship the bot has with your data: from "we hold this so the product works" all the way down to "we never see it".

Everything in the stored cards got there through something you did yourself: starting the bot, joining a meeting, creating one, changing a setting. If a meeting is shared into a chat you're in, or someone adds your name to a guest list, the bot stores nothing about your Telegram account.

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
        <div class="tldr-row__why">Replies in your preferred language (English until you change it in Settings).</div>
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
        <div class="tldr-row__why">The meetings you've joined yourself from a shared card, and whether you're a confirmed participant or on the waiting list.</div>
      </div>
      <div class="tldr-row">
        <div class="tldr-row__label">Meeting locations</div>
        <div class="tldr-row__why">Coordinates attached to a meeting by the owner so guests can navigate there.</div>
      </div>
      <div class="tldr-row">
        <div class="tldr-row__label">Bot's own messages<span class="tldr-row__flag"><span class="tldr-pill"><span class="dot"></span>Temporary</span></span></div>
        <div class="tldr-row__why">Chat, message, and inline-message IDs for bot-sent messages, plus the button layout the bot drew (which contains no user text). Never the message text itself. Kept so the bot can edit or delete those messages when something changes.</div>
      </div>
      <div class="tldr-row">
        <div class="tldr-row__label">Guests you've invited</div>
        <div class="tldr-row__why">The name you typed when adding a friend to a meeting, plus a note that you added them, so the card can show who invited whom. That entry holds a spot on the list and is never linked to any Telegram account.</div>
      </div>
    </div>
  </section>

  <section class="tldr-card tldr--stored">
    <header class="tldr-card__head">
      <div>
        <h3>If you link Patreon</h3>
        <p>Exists when you link your Patreon account through the bot's Collaborate screen, to back Mitup as a Host. Unlinking deletes the link record straight away. Starting the flow also creates a short-lived pending record, described below.</p>
      </div>
      <span class="tldr-pill"><span class="dot"></span>Stored</span>
    </header>
    <div class="tldr-rows">
      <div class="tldr-row">
        <div class="tldr-row__label">Patreon account ID</div>
        <div class="tldr-row__why">Matches your Telegram account to your Patreon membership. The completed link holds the numeric ID and nothing else from your Patreon profile. The short-lived pending record also holds your Patreon display name, so the confirmation prompt can show you which account it is.</div>
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
        <div class="tldr-row__label">People who see a shared card</div>
        <div class="tldr-row__why">A meeting card landing in your group stores nothing about anyone there. The bot first learns you exist when you start it or join a meeting yourself.</div>
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

1. The bot sends you to Patreon, where you approve the connection on Patreon's own page. That page carries nothing about who you are on Telegram, and it stops working after fifteen minutes.
2. Patreon hands the bot a one-time sign-in token. The bot uses it once, to ask Patreon "which account is this, and are they a member of the Mitup campaign?", then discards it.
3. Nothing is connected yet. The bot sets the answer aside for fifteen minutes and shows you a confirmation link. Opening that link from Telegram brings up a prompt naming the Patreon account it would connect, and nothing is written until you tap to confirm. That prompt is the point: you should only ever see it straight after approving Mitup on Patreon yourself, so if one turns up when you didn't just do that, somebody else's link reached your chat. If you already back Mitup it also tells you which tier confirming would cost you. Read it before you confirm, and tap *Not now* if it isn't yours.
4. While the confirmation is pending we hold the Patreon account ID, the Patreon display name the prompt shows you, and a scrambled copy of the link's code, never the code itself. Once you open the link from Telegram the pending record also holds your Telegram ID, so we know whose confirmation to expect. It stops being usable once you confirm or the fifteen minutes run out, and a daily cleanup deletes spent and expired records. Tapping *Not now* connects nothing and leaves the link usable until it expires, in case you change your mind. If you ask us to delete your account, any pending record you opened goes with it; one you never opened has no account attached, so the daily cleanup is what removes that.
5. The bot saves your numeric Patreon account ID, your tier, and when your current membership period ends. That is the whole record.

We never see your payment details, your card, your address, or your Patreon email. Payment stays entirely between you and Patreon. Unlinking from the same Collaborate screen deletes the link record immediately and returns your account to the free limits.

## Why we're allowed to process this

For the GDPR-minded, the legal bases are the boring ones:

* **Performing the service you asked for** covers everything in the tables above: your Telegram identity, your preferences, your meetings and RSVPs, and the Patreon link you set up yourself.
* **Legitimate interest** covers operational logs and keeping the bot safe and running.
* Nothing is processed for advertising, profiling, or sale. There is no basis to look for because the processing doesn't exist.

## How long we keep your data

**Your user record** persists while you're an active Mitup user. If you were once a member and then block the bot (or if a message we send to you fails), we set a flag on your account. A cleanup job runs periodically and deletes flagged accounts once they no longer own or participate in any active meeting, which cascades to your settings, owned meetings, and RSVPs.

If you joined a meeting via someone else's group message without ever opening the bot directly, your record exists only for the lifetime of those meetings and is removed when they end.

**Meetings you've joined** (RSVPs): when the meeting is deleted or you leave it, your RSVP is removed. A guest someone added by name is a placeholder entry on the meeting, never linked to any Telegram account, and disappears with the meeting.

**Meetings you own:** when a meeting becomes inactive, how long it's kept afterward, and when it's permanently deleted are covered on the [Meeting lifecycle](../user-guide/meeting_lifecycle.md#how-long-an-inactive-meeting-is-kept) page.

**Your Patreon link** exists while you keep it. Unlink from the Collaborate screen and the link record is deleted on the spot. Deleting your user record removes it too, along with any pending record you opened from Telegram. A pending record you never opened holds a Patreon account ID but nothing connecting it to your Mitup account, so we have no way to tell it was yours; the daily cleanup removes it once it expires.

**Operational logs.** Every interaction with the bot writes diagnostic logs so we can keep the service running and debug problems. Every log line carries your Telegram numeric ID. The metric log lines additionally record your username, the text of messages you send to the bot, the buttons you tap, and the text of your inline searches. Never the content of your other Telegram chats. If you link Patreon, the linking flow also logs your numeric Patreon account ID. Logs delete themselves automatically: application and background-job logs after 30 days, maintenance and deployment logs after 30 days (these contain no user data), and database engine logs after 90 days. Routine statement logging is off on the database, so user data reaches those logs only incidentally, in error diagnostics. The legal basis is our legitimate interest in operating, debugging, and securing the service. Logs are never used for profiling, analytics, or advertising.

## Your rights and how to exercise them

**Access and export.** Tap *🛡️ Privacy*{.button-like} under *⚙️ Settings*{.button-like}, then *📦 Export my data*{.button-like}. The bot replies with a JSON file containing everything Mitup stores about you: your user record, your settings, every meeting you own, every meeting you've joined, your Patreon link if you have one, and any Patreon confirmation you have opened but not yet completed. Other people in your meetings appear only by the display name they show on Telegram, meaning their @username or first name. If you've blocked the bot, unblock it, send the `/start` command to reactivate your account, and the export button works again. If something else is in the way, email `privacy@mitup.social` and we'll figure it out together.

**Rectification.** Edit your display name, language, or timezone directly in the bot's settings menu. For other corrections, email us.

**Erasure.** Tap *🛡️ Privacy*{.button-like} under *⚙️ Settings*{.button-like}, then *🗑️ Delete my data*{.button-like}, and confirm twice. Once confirmed, your account is marked for deletion and stops working right away, and within a day your user record, every meeting you own, every RSVP you've made, your Patreon link, and any Patreon confirmation you had opened are permanently removed. Your Telegram ID and username may still appear in short-lived operational logs after that; those expire automatically within the retention periods above (at most 90 days, and 30 for application logs) and are never used to reconstruct an account. You get one final message confirming it's done. If you ever want to use Mitup again, send the `/start` command in your chat with the bot and it sets you up from scratch, with no memory of your old account.

**Portability.** The export is plain JSON, formatted for import into another system if you wish.

**Right to object.** If you believe Mitup is processing your data unfairly, email us.

**Complaints.** If you believe your rights have been infringed, you can also lodge a complaint with your local data protection supervisory authority. In the EU, that's the authority of the country you live in.

**Who the controller is.** Mitup is maintained by individuals based in Spain, not a registered company. They decide what data is collected and why, which makes them the data controller in GDPR terms. The maintainers monitor `privacy@mitup.social` and respond to data-subject requests and supervisory-authority enquiries there. You can raise any concern via that email or by opening an issue in the [GitLab repository](https://gitlab.com/meetupbot/mitup-telegram-bot/-/issues). Given Mitup's scale and non-commercial nature, a Data Protection Officer is not required; `privacy@mitup.social` is the single contact point for all data-protection matters.

## Security

**In transit.** All communication between your client and Mitup's servers uses TLS encryption.

**At rest.** Mitup's database is a managed PostgreSQL instance on AWS, in the Ireland region (eu-west-1), inside the EU. Storage is encrypted at rest with an AWS-managed key. Database backups are retained for 7 days. A final snapshot is taken if the instance is ever destroyed. There is no cross-region replication.

**Secrets management.** API keys and database credentials live in AWS Systems Manager Parameter Store (SecureString), not in code. Access is gated by IAM.

**What we don't do.** We don't undergo regular third-party security audits or hold formal certifications like SOC 2. We rely on AWS's infrastructure and standard operational hygiene.

**Reporting.** If you find a security issue, email [security@mitup.social](mailto:security@mitup.social). The same address is published at [mitup.social/security.txt](https://mitup.social/security.txt).

## Third parties

Mitup shares your data only with:

* **Telegram.** The bot sends messages to your account and receives messages from you via Telegram's infrastructure. Telegram operates its own international messaging infrastructure and acts as the independent controller of the messaging layer.
* **Google (Google Maps Platform).** When you set up your timezone, by typing a city name or sending a location pin, we use the Google Maps Time Zone API to resolve coordinates into an IANA timezone string. When you type an address rather than sending a pin, we also use the Google Maps Geocoding API to resolve that address into coordinates. We send only the address text or the coordinates themselves. We never send your Telegram ID, your name, or any other piece of your profile. Google receives a one-off lookup query that cannot be tied back to you by us. Google is established in the United States; transfers of these lookup queries rely on Google's EU-U.S. Data Privacy Framework certification and/or Standard Contractual Clauses.
* **Patreon.** Only if you link your Patreon account. The bot asks Patreon for your account ID, the display name on that account, and your membership status in the Mitup campaign, nothing more. The display name is used to show you which account you are about to connect. It is held only on the short-lived pending record described above, never on the Patreon link itself. Your pledge, payment method, and billing details live on Patreon and never reach the bot. Patreon is established in the United States; transfers of the limited data it receives rely on Patreon's EU-U.S. Data Privacy Framework certification and/or Standard Contractual Clauses. See [how Patreon linking works](#how-patreon-linking-works).
* **AWS.** Your data lives on Amazon Web Services (AWS) infrastructure in the EU. AWS processes it on our behalf as an infrastructure provider, under AWS's standard GDPR data processing addendum. We decide what is stored and how it's used.

No analytics firms, ad networks, or third-party tracking services have access to your data.

## Breaches

If user data is exposed due to a breach in Mitup's infrastructure or a compromise of the bot's credentials, we will:

1. Notify affected users via Telegram message as soon as we're aware.
2. Post a summary on this page.
3. Where the breach is likely to result in a risk to your rights, notify the competent supervisory authority within 72 hours of becoming aware of it (GDPR Article 33) and notify affected users without undue delay (Article 34).

## If Mitup shuts down

If the project ceases operations, all user data in the database will be deleted. We'll announce the shutdown via a message from the bot and on this page at least 30 days in advance.

## Changes to this policy

Material changes to this policy will be announced via a message from the bot and on this page. The revision date below shows the last update.

---

**Last revised:** July 16, 2026

---

## Questions or concerns?

Email `privacy@mitup.social` or open an issue on [GitLab](https://gitlab.com/meetupbot/mitup-telegram-bot/-/issues).

For more about who maintains Mitup, visit the [About us](about.md) page.
