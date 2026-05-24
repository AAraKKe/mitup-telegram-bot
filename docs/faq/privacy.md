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
        <div class="tldr-row__why">The events you've made, with their title, time, options, and RSVP list.</div>
      </div>
      <div class="tldr-row">
        <div class="tldr-row__label">Meetings you've joined</div>
        <div class="tldr-row__why">The events others invited you to and whether you accepted, declined, or are on the waiting list.</div>
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
        <div class="tldr-row__why">Record of which member invited you to a meeting.</div>
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

When you set up your timezone, you can type a city name or send a Telegram location pin. We use the Google Maps APIs to look up your timezone from either the text or the coordinates. Here's exactly what happens in each case:

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

## How long we keep your data

**Your user record** persists while you're an active Mitup user. When Telegram tells us you've blocked the bot (or when a message we send to you fails), we set a flag on your account. A cleanup job runs periodically and deletes all flagged users, which cascades to your settings, owned meetings, and RSVPs.

**Meetings you own:** once a meeting's end time plus your configured timeout has passed, the meeting is marked inactive. (If a meeting has no datetime set, it's marked inactive one year after creation.) When a meeting goes inactive, invited-only users (people who were added to the meeting but don't have a Mitup account themselves) are deleted, and so are the meeting's messages.

After 173 days of inactivity, you'll receive a notification that the meeting will be permanently deleted in 7 days. You can reactivate it at any point during the 180-day window to reset the clock. After 180 days of inactivity, the meeting is permanently deleted automatically (including all RSVPs).

**Meetings you're invited to** (RSVPs): when the meeting is deleted or you leave it, your RSVP is removed.

**Explicit deletion:** if you delete a meeting directly, it's removed immediately with no grace period.

## Your rights and how to exercise them

**Access and export.** Email `privacy@mitup.social` and we'll send you a JSON export of all your data: your user record, every meeting you own, and every RSVP you've made.

**Rectification.** Edit your display name, language, or timezone directly in the bot's settings menu. For other corrections, email us.

**Erasure.** Tap *🛡️ Privacy*{.button-like} from the main menu. You'll see a "Delete my data" button. Tap it and confirm twice. Once confirmed, your user record, every meeting you own, and every RSVP you've made are removed permanently within seconds.

**Portability.** Email `privacy@mitup.social` and we'll send your data as JSON, formatted for import into another system if you wish.

**Right to object.** If you believe Mitup is processing your data unfairly, email us.

**No legal entity.** Mitup is maintained by individuals, not a registered company. There is no formal data controller structure. If you have concerns the maintainers don't resolve, you can raise a complaint via email or by opening an issue in the [GitLab repository](https://gitlab.com/meetupbot/mitup-telegram-bot/-/issues).

## Security

**In transit.** All communication between your client and Mitup's servers uses TLS encryption.

**At rest.** Mitup's database is a managed PostgreSQL instance on AWS. Storage is encrypted at rest with an AWS-managed key. Database backups are retained for 7 days. A final snapshot is taken if the instance is ever destroyed. There is no cross-region replication.

**Secrets management.** API keys and database credentials live in AWS Systems Manager Parameter Store (SecureString), not in code. Access is gated by IAM.

**What we don't do.** We don't undergo regular third-party security audits or hold formal certifications like SOC 2. We rely on AWS's infrastructure and standard operational hygiene.

## Third parties

Mitup shares your data only with:

* **Telegram.** The bot sends messages to your account and receives messages from you via Telegram's infrastructure.
* **Google (Google Maps Platform).** When you set up your timezone or attach a location to a meeting, we use the Google Maps Time Zone API to resolve coordinates into an IANA timezone string. When you type an address rather than sending a pin, we also use the Google Maps Geocoding API to resolve that address into coordinates. We send only the address text or the coordinates themselves. We never send your Telegram ID, your name, or any other piece of your profile. Google receives a one-off lookup query that cannot be tied back to you by us.
* **AWS.** Your data lives on managed AWS services (ECS where the bot runs, RDS for the Postgres database, Lambda for migrations, SSM Parameter Store for secrets, CloudWatch for operational logs, S3 and CloudFront for this documentation site). AWS is an infrastructure provider, not a data processor in the GDPR sense. We control what data is stored and how it's used.

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

**Last revised:** May 24, 2026

---

## Questions or concerns?

Email `privacy@mitup.social` or open an issue on [GitLab](https://gitlab.com/meetupbot/mitup-telegram-bot/-/issues).

For more about who maintains Mitup, visit the [About us](about.md) page.
