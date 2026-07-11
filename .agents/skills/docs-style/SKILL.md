---
name: docs-style
description: Documentation style conventions for mitup_bot. Auto-load when writing or editing files in docs/.
user-invocable: false
---

# Documentation Style

Docs in `docs/` are served by Zensical (an MkDocs-compatible Python CLI). Config lives at `zensical.toml` in the repo root. Custom CSS lives in `docs/assets/stylesheets/main.css` (theme overrides + landing-page components) and `docs/assets/stylesheets/mitup-components.css` (chat showcases, button-like, admonition restyle).

A working canon to model yourself on lives in three pages: `docs/index.md`, `docs/faq/privacy.md`, `docs/collaborate/donation.md`. When in doubt about voice or structure, read those first.

## Voice

Mitup talks like a friend, not a product. Friendly and a little playful, never theatrical. Imagine the maintainer answering a question from a friend who asked how the bot works — warm because the words are warm, not because every sentence ends in an exclamation mark. The goal is "talking with a friend", not "talking with someone who's had too much coffee".

* **Friendly, not enthusiastic.** Warmth comes from being specific and helpful, not from `exciting`, `awesome`, `amazing`, or trailing exclamation marks. If a sentence would make the reader picture a brand mascot, rewrite it.
* **Plain and declarative.** Short sentences. Second person. Say the thing, then move on.
* **Specific over generic.** Real examples, real names, real numbers. `"Weekend Hike Prep", "Board Game Night", "Ana's Birthday Drinks"` beats `"your meetings"`. `~62% covers servers, managed Postgres, and email infrastructure` beats `most of the money goes to operations`. Vary scenarios: brunch, climbing, board games, birthdays, online game nights, book club. Never `team standup`, `OKR`, `sprint review` — Mitup is for friend groups, not offices.
* **Tongue-in-cheek is allowed when the joke is concrete.** `the occasional pizza for whoever's debugging migrations at 1am` works because it commits to a detail. `The warm fuzzies, monthly` works because it deflates a tier list. Vague enthusiasm (`our amazing community`, `exciting new features`) doesn't.
* **Don't oversell, don't apologize.** Skip framing like *Mitup provides a powerful set of tools to…*. Skip openers like *Thank you for considering…*. Open with the thing the reader came for.
* **End when you're done.** No closing thank-you, no `Remember, you can always…`, no `Thank you for being part of our journey.` The page ends where the last fact ends.

| Don't say | Say |
|---|---|
| `Unlock powerful event management` | `Create a meeting in four taps.` |
| `Robust privacy infrastructure` | `Mitup never joins your group.` |
| `Click here` | `Tap *➕ New meeting*{.button-like}` |
| `Welcome! We're so excited to have you!` | `First time? Here's how it works.` |

## Anti-patterns

These show up across `docs/user-guide/*.md`, `docs/collaborate/supporter.md`, and `docs/collaborate/translator.md`. They are AI tells. Treat them as bugs and remove them when you see them, even in sections you weren't sent to edit (see the proactive-cleanup rule in the docs-writer agent).

### 1. Title Case headings

The good pages use sentence case. Title Case is the strongest single AI tell in this repo.

* **Don't:** `# Configure Your Meeting`, `## Default Meeting Options`, `## Where Your Money Goes`
* **Do:** `# Configure a meeting`, `## Default meeting options`, `## Where your money goes`

Exception: proper nouns and acronyms keep their casing (`# Telegram Stars`, `# MIT License`).

### 2. Emojis in documentation pages

**Documentation pages don't use emojis.** Not on headings, not in body prose, not as decoration. The only place an emoji is allowed in a docs page is inside a `.button-like` chip, because it has to mirror the real bot button (see anti-pattern 12 and the `.button-like` component).

* **Don't:** `## Language settings :earth_americas:`, `## Notifications :bell:`, `Just tap :gear: to open settings.`, `🎉 Welcome to Mitup`.
* **Do:** plain `## Language settings`, plain `Open *⚙️ Settings*{.button-like} from the main menu.`

Font Awesome shortcodes (`:fontawesome-solid-…:`, `:fontawesome-brands-…:`) are **not** emojis for this rule. They're component icons used inside `.grid cards` headers, `.md-button` CTAs, and the social links block in `zensical.toml`. Those stay. The rule is about Unicode emoji and Twemoji shortcodes in headings or body prose.

### 3. Forced enthusiasm and exclamation marks

* **Don't:** `That's it!`, `Creating a new meeting is simple!`, `Just send a message`, `No problem!`, `Mitup provides a powerful set of tools to tailor it exactly to your needs.`
* **Do:** state the steps. The reader knows it's simple if it's three lines long.

A page can have one exclamation mark if it earns it. Two is usually one too many.

### 4. Em-dashes

The em-dash (`—`) is the single most visible AI fingerprint in prose. It is almost never the right pause. Almost every em-dash can become a period, a comma, a colon, or parentheses, and the sentence reads more direct after.

* **Don't:** `Mitup runs as a private DM with each user — no one in your group sees the bot.`
* **Do:** `Mitup runs as a private DM with each user. No one in your group sees the bot.`
* **Don't:** `Tap the button — found in the main menu.`
* **Do:** `Tap the button in the main menu.`

If a sentence really needs a strong mid-sentence break, restructure it into two sentences. Hyphens in compound words (`mid-sentence`, `dark-navy`) and en-dashes (`–`) in numeric ranges (`1–3 words`) are fine; the rule is about the em-dash (`—`) only.

### 5. Closing thank-yous and warm wrap-ups

* **Don't:** `Thank you for being part of our journey.`, `Thank you for helping make Mitup a global service!`, `Remember, you can always come back and adjust these settings as needed!`
* **Do:** end on the last useful sentence. If there's a real next action, link to it. Otherwise stop.

### 6. Filler intros and meta-sentences

* **Don't:** `This guide explains all the settings available in Mitup and how to configure them through Telegram. Proper configuration ensures you get the best experience…`, `Here's a breakdown of what you can configure:`, `Here's the step-by-step process:`
* **Do:** open with the first concrete instruction or the first concrete fact. The H1 already tells the reader what the page is.

### 7. Hollow positivity vocabulary

Words that almost always signal AI padding: `powerful`, `seamless`, `amazing`, `exciting`, `simply`, `easily`, `truly`, `comprehensive`, `robust`, `journey`, `empower`, `unlock`. Also the phrase `it is highly recommended`.

* **Don't:** `our amazing community`, `exciting new features`, `seamlessly schedule meetings`, `It is highly recommended to run validate before pushing.`
* **Do:** `the people who translate Mitup on Crowdin`, `the next features on the roadmap`, `Run validate before you push. CI runs the same checks and rejects MRs that fail.`

### 8. Vague intensifiers used as filler

`just`, `simply`, `easily`, `really`, `actually`. Almost always cuttable.

* **Don't:** `Just head to @mitupbot and start a conversation`, `simply install postgresql`, `you can easily change the title anytime`
* **Do:** `Open [@mitupbot](https://t.me/mitupbot) and start a conversation`, `install postgresql with Homebrew`, `change the title anytime`

### 9. Repeated `**How to X:**` scaffolding

When every section opens with bold-colon `**How to set your timezone:**` followed by a numbered list, the page reads like a template. Vary it. A two-sentence paragraph followed by a list does the same job and looks human.

### 10. Hedged parenthetical disclaimers

* **Don't:** `(This feature might still be under development)`, `(though you can modify them for individual meetings)` tacked onto unrelated sentences.
* **Do:** if a feature is half-shipped, use a `!!! note "Under development"` admonition. If a fact has a meaningful caveat, give it its own sentence.

### 11. Numbered headings for parallel sections

* **Don't:** `### 1. Donations`, `### 2. Translations`, `### 3. Code contributions`
* **Do:** `### Donations`, `### Translations`, `### Code contributions`. The order is already visible in the source.

### 12. Bot buttons in prose without `.button-like`

Every mention of a bot button in body text uses the `.button-like` inline chip (see the component catalogue below). Plain bold, plain italics, monospace, or a quoted name don't match what the user sees on the screen.

* **Don't:** `Tap **New meeting**.`, `Press the "New meeting" button.`, `` Tap `New meeting`. ``, `Tap New meeting (➕).`
* **Do:** `Tap *➕ New meeting*{.button-like}.`

This is the *only* place an emoji belongs in a docs page (see anti-pattern 2). The emoji must be the **raw Unicode glyph** copied verbatim from the matching `ButtonMessages` entry in `mitup_bot/utils/messages.py` (which sources `libs/core/mitup_bot/locales/<lang>.po`). Don't use Twemoji shortcodes like `:heavy_plus_sign:` — shortcodes can render differently from the real button (e.g. `:heart:` renders as `❤️` but the bot button is `♥`), so they break the "this is what's on your phone" promise of `.button-like`.

### 13. Developer language on user-facing pages

User-facing pages (`docs/index.md`, `docs/user-guide/*.md`, `docs/faq/*.md`, `docs/collaborate/*.md`) are written for ordinary people who know Telegram, not the codebase. The reader is learning how the bot works today, from their side of the screen. Never leak the implementation.

* **No code identifiers or internal names.** Never write a variable, field, or setting name from the source (`notification_time`, `allow_invitation`, `lock_on_start`, `is_in_progress`, `timeout`). Describe the thing the way the user meets it: "the reminder before it starts", "the Open invitations option", "a few minutes after it finishes".
* **No invented UI jargon.** Don't coin names the bot never shows the user (`sub-hub`, `config panel`). Name screens by what the button or heading actually says: "the When menu", "the menu", "Settings".
* **Say what the user does, in their words.** The user sends a *location*, never *coordinates*. They *type a title*, they don't *submit input*.
* **Don't** `Set your notification_time under Notifications.`, `Turn on lock_on_start in the When sub-hub.`, `The bot reads your coordinates.`
* **Do** `Choose how long before the start you get your reminder under *⏰ Notifications*{.button-like}.`, `Turn on lock on start in the When menu.`, `The bot reads the location you send.`

The one exception is the developer handbook under `docs/contribute/`. It is written for contributors, so source identifiers, config keys, and internal terms are expected there. This anti-pattern is about the user-facing pages only.

### 14. Development-history leaks

Describe what the bot *is* today, not how it got there. A reader learning the bot doesn't want its change log, and "used to be" phrasing dates the page the moment it ships.

* **Don't:** `This lives in the When menu now, not in Settings.`, `Lock on start was moved out of the options screen.`, `This used to clear the time, but now it keeps it.`
* **Do:** `Lock on start lives in the When menu.`, `The date and time are kept.`

State the current behaviour plainly. If two things are easy to confuse, contrast them by where they *are* ("Lock on start lives in the When menu, the other four options live in Settings"), never by where they *were*.

## Product terminology

Match the words the bot itself uses. These apply to the user-facing pages (see anti-pattern 13); the `docs/contribute/` handbook follows the codebase's terms instead.

* **Meeting state is "active" or "inactive".** That mirrors what the bot says (it marks a finished meeting as inactive / deactivated). A meeting that has finished is *inactive*, not "past" and not "gone by".
  * **Don't:** `Once a meeting goes past, its buttons disappear.`, `Past meetings are read-only.`
  * **Do:** `Once a meeting becomes inactive, its buttons disappear.`, `Inactive meetings are read-only.`
  * The one exception is the literal button label *💾 Your past meetings*{.button-like}, which uses the word "past". It's the real button name, so refer to the button by its name, but describe the underlying state as inactive: "Your inactive meetings live under *💾 Your past meetings*{.button-like}." Always confirm the exact glyph and text in `ButtonMessages` before writing the chip.
* **Link the bot handle every time.** Every `@mitupbot` in prose is a link to `https://t.me/mitupbot`, so a reader can tap straight through. A bare `@mitupbot` with no link is a bug.
  * **Don't:** `Open @mitupbot and start a conversation.`
  * **Do:** `Open [@mitupbot](https://t.me/mitupbot) and start a conversation.`
  * This is separate from the `mitupbot` alias used inside chat showcases (see the chat-showcase rules), which is a plain header label, not a link.

## Headings

* Sentence case (see anti-pattern 1).
* Always surround headings with blank lines. Bullets and code fences directly below an unspaced heading won't render correctly.
* The H1 comes from the file (`# Page title`), not from the nav. Don't duplicate the nav label.

```markdown
## This heading
- Won't
- Properly generate
- The bullet point list

## But this heading

- Will do it
- Properly
```

The same blank-line rule applies to bullet lists themselves: surround them with blank lines.

## File conventions

* New files use `snake_case.md`.
* Every file ends with a single empty line.
* Every page has YAML front matter with a Material icon for the nav: `icon: material/xxx-outline` (e.g. `material/gift-outline`, `material/shield-lock-outline`, `material/cog-outline`). Browse icons at <https://pictogrammers.com/library/mdi/>.
* When adding a new page, register it in the `nav` array of `zensical.toml`.
* Bullets use `*`, not `-`. Some environments render `-` inconsistently; `*` always works.
* Links to non-documentation files (source code, configs) must use the full GitLab URL on `main`: `https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/<path>`. The docs site doesn't ship project files.

## Icon vocabulary: Twemoji vs Font Awesome vs Material

The repo uses three icon systems, each in its own slot. Don't mix them up.

* **Material icons.** Page front-matter `icon:` only. Format: `material/xxx-outline`. Drives the nav sidebar. Browse icons at <https://pictogrammers.com/library/mdi/>.
* **Font Awesome shortcodes.** Component icons only: `.md-button` CTAs, `.grid cards` headers, and the social icons block in `zensical.toml`. Format: `:fontawesome-solid-paper-plane:`, `:fontawesome-solid-language:`, `:fontawesome-brands-gitlab:`. Use solid for actions, brands for logos. Not for decorating headings or body prose.
* **Twemoji shortcodes.** Not used in this codebase. `.button-like` chips use **raw Unicode glyphs** copied verbatim from `ButtonMessages` (see anti-pattern 12 and the `.button-like` recipe). Shortcodes are forbidden because they can render differently from the real bot button (e.g. `:heart:` renders as `❤️` but `ButtonMessages.COLLABORATE` is `♥`).
* **Unicode emoji in prose.** Forbidden. The only place a raw glyph belongs is inside a `.button-like` chip.

## Validation

After modifying any documentation file, validate the build:

```bash
uv run mb docs build
```

For an iterative preview, run `uv run mb docs serve` (`zensical serve`) and open the URL it prints.

## Brand reference

You almost never need to touch brand styling from a docs page — that's the job of `main.css` and `mitup-components.css`. But when you do (custom hero, new landing-style page, inline color), pick from the tokens already defined in `main.css`. Don't invent hex codes.

* **Mitup Blue** `var(--blue)` `#2AA1E0` — primary action, links, accents. Hover: `var(--blue-deep)` `#1F87BF`. Tinted surfaces: `var(--blue-soft)` `#E8F5FD`.
* **Moment Yellow** `var(--yellow)` `#FFC850` — *rare and precious*. Used for the i-dot in the wordmark, The Moment in the mark, the warning admonition. Never a primary surface, never large text. Yellow text on light backgrounds uses `var(--yellow-d)` `#B58A1F`.
* **Ink** `var(--ink)` `#0E1A23` body / `var(--ink-2)` `#36505F` secondary / `var(--ink-3)` `#7A8C97` tertiary.
* **Paper** `var(--paper)` `#F6FAFD` page bg / `var(--paper-2)` `#FFFFFF` cards. Borders: `var(--line)` `#E2ECF2`.
* **Patreon** `var(--patreon)` `#F0511C` — only inside `.patreon-strip`.

Typography: Inter (400–900) for everything, JetBrains Mono (400/500) for code and eyebrow labels. The `font-feature-settings: "ss01", "cv11"` plus antialiasing is set globally — don't override. Sentence case everywhere except the lowercase wordmark. No Inter italics for emphasis; use bold.

If you ever need the long-form rationale (mark construction, lockup clearspace, what counts as misuse), it's in the standalone brand guide that ships with the brand assets bundle. The skill stays the authority for docs writing when they conflict.

## Component catalogue

The docs ship a small library of reusable components. Don't roll your own; use these whenever the situation matches. Components are styled by `main.css` and `mitup-components.css`.

### Page sections built from HTML

`md_in_html` is enabled, so HTML blocks can contain Markdown, but only if you opt in. Any `<div>`, `<section>`, or `<figure>` that wraps Markdown content needs `markdown` on the outer element (or `markdown="span"` for inline content). Without it, Markdown inside the block is rendered literally.

```html
<div class="grid cards" markdown>

- :fontawesome-solid-language: **10+ languages**

    ---
    Translated by the community.

</div>
```

### `.hero`

The landing-page opener used on `index.md`. Centered eyebrow chip, oversized H1 (`{.hero-title}` for the special hero typography), supporting paragraph, CTA row, and a meta row of small stats. Use only on landing-style pages (the index, optionally a top-level section landing). Don't put a hero on user-guide pages.

Anatomy: `.hero > .eyebrow + h1.hero-title + p + .hero-ctas + .hero-meta`.

### `.demo-section`

Full-bleed dark showcase. Used for the meeting-flow animation on `index.md`. Black background with a radial blue glow, centered title (`.demo-title`), centered subtitle (`.demo-sub`), and a `.demo-frame` that holds an `<iframe>` (animations live in `docs/animations/`). Heavy visual weight; one per page max.

### `.privacy-strip`

Dark navy card with white text and a primary `.md-button` inside. Currently used on `index.md` to point at the privacy page. Good for any "by the way, here's a serious thing you should read" moment on a marketing-style page. Don't use as a generic callout; that's what admonitions are for.

### `.patreon-strip` + `.patreon-tier`

Cream gradient card that holds tier pricing. Two-column grid (text on the left, three tiers on the right). Mark the middle tier `.patreon-tier.featured` to highlight it. Re-used on `index.md` and `collaborate/donation.md`.

### `.grid cards`

Native mkdocs-material grid for feature lists. Each card is a list item with a Font Awesome icon in the header, a `---` separator, then body Markdown. Re-styled by `main.css` to round and lift on hover.

```markdown
<div class="grid cards" markdown>

- :fontawesome-solid-bell: **Smart reminders**

    ---
    Get alerts in your own timezone before the event starts.

- :fontawesome-solid-earth-americas: **Timezones, handled**

    ---
    Share events across the world without confusion.

</div>
```

### `.badge`

Status chip for table cells (and inline prose). Four variants: `yes` (green), `no` (yellow), `info` (blue), `danger` (red). See `faq/privacy.md` for the canonical example.

```markdown
| `user_id` | <span class="badge yes">stored</span> |
| `location_pin` | <span class="badge no">used once, discarded</span> |
```

### Tables

Plain Markdown tables render as a rounded card with a header row and hover-tinted rows. Two-column tables auto-right-align the value column for a key/value feel. Don't add HTML wrappers, classes, or inline styles; let the default styling work.

### `.button-like`

**Use this whenever the prose names a bot button the user should tap. No exceptions.** Plain bold, plain italics, backtick monospace, or a quoted name are all wrong (see anti-pattern 12).

Recipe:

1. Identify the button mentioned (e.g. "New meeting").
2. Find the matching entry in `ButtonMessages` in `mitup_bot/utils/messages.py` to confirm the exact text and emoji. The actual string lives in `libs/core/mitup_bot/locales/en.po` under the corresponding `msgid`. Example: `ButtonMessages.NEW_MEETING` → `"➕ New meeting"`.
3. Copy the emoji glyph **verbatim**. Do **not** convert to a Twemoji shortcode. Shortcodes can render a different glyph from what the bot actually sends (e.g. `:heart:` → `❤️`, but the bot button is `♥`), and the whole point of `.button-like` is to mirror what the user sees on their phone.
4. Format as `*<glyph> Button Text*{.button-like}`.
   * Example: `*➕ New meeting*{.button-like}`
5. Rules:
   * Wrap in Markdown italics (`*...*`).
   * **Never** use backtick monospace.
   * The `.button-like` class lives inside the closing asterisk, not outside.
   * Sentence-case the button text as it appears in the bot.
   * Keep the emoji as a raw glyph. `.button-like` without the emoji isn't faithful to the real button, and a Twemoji shortcode may not match either.

The CSS renders a soft blue chip with deep-blue text; no extra styling needed. Two chips on adjacent wrapped lines are sized to never touch.

### Chat showcases: `.mitup-phone` and `.mitup-annotated`

Two reusable HTML components render the bot interface. **Pick by use case, never write a chat mockup from scratch.**

`.mitup-phone` is the full phone bezel with status bar, header, message, inline keyboard, and a Telegram input bar. Heavier visual weight. Use for:

* Hero shots on landing or feature pages
* Animations / multi-step flow stills
* The first showcase on a page that introduces a feature

`.mitup-annotated` is the same chat without the bezel, plus labelled annotation chips in the side gutters that point at parts of the UI. Use for:

* User-guide screenshots that walk through what the user sees
* Explainer pages in the FAQ
* Any time you want to label "this is the inline keyboard", "this is the input bar", etc.

Minimal template for the phone bezel (drops straight into a Markdown page because `md_in_html` is on):

```html
<div class="mitup-phone">
  <div class="mitup-phone__screen">
    <div class="mitup-phone__status">
      <span>9:41</span>
      <div class="mitup-phone__notch"></div>
      <span class="mitup-phone__signal"><span>5G</span><span class="mitup-phone__battery"></span></span>
    </div>
    <div class="mitup-chat-header">
      <div class="mitup-chat-header__back">‹</div>
      <div class="mitup-avatar"><!-- Mitup mark SVG --></div>
      <div>
        <div class="mitup-chat-header__name">mitupbot</div>
        <div class="mitup-chat-header__sub">bot · online</div>
      </div>
    </div>
    <div class="mitup-phone__body">
      <div class="mitup-bot-msg">
        <div class="mitup-bot-msg__content">
          <div class="mitup-bot-msg__sender">mitupbot</div>
          <div class="mitup-bot-msg__text">
            <strong>Welcome to Mitup Bot!</strong><br/>
            Choose one of the following options:
          </div>
        </div>
        <div class="mitup-bot-msg__keyboard">
          <div class="mitup-bot-msg__row"><div class="mitup-key">➕  New meeting</div></div>
          <div class="mitup-bot-msg__row mitup-bot-msg__row--2">
            <div class="mitup-key">👥  Joined meetings</div>
            <div class="mitup-key">⚙️  Settings</div>
          </div>
        </div>
      </div>
    </div>
    <div class="mitup-chat-input">
      <div class="mitup-chat-input__menu">≡</div>
      <span class="mitup-chat-input__attach">📎</span>
      <span class="mitup-chat-input__placeholder">Write a message…</span>
    </div>
  </div>
</div>
```

**Inline keyboard rows**: multi-column rows use `.mitup-bot-msg__row--2` or `.mitup-bot-msg__row--3` on the row element.

**Annotated variant**: wrap the chat in `.mitup-annotated` → `.mitup-annotated__chat`, and put `.mitup-annotation` chips as direct children of `.mitup-annotated` with `style="top: NNpx"` and either the `--left` or `--right` modifier:

```html
<span class="mitup-annotation mitup-annotation--left" style="top: 28px;">
  <span class="mitup-annotation__label">Bot message</span>
  <span class="mitup-annotation__line"></span>
</span>
```

The annotation chips render in the 70 px gutter on either side of the chat. Keep labels short, 1–3 words.

**Bot alias** in the chat header and bubble sender is always `mitupbot` (the real bot handle, lowercase). No `MitupStaging`, no `Mitup Bot`, no environment variants.

**User names in chat showcases must be fictitious.** Use plausible first names or first-name-plus-surname, varied across pages. The canon examples are `Ana`, `Ana Marín`, `Marta`, `Diego`, `Sara`, `Tomás` (already used in the home-page animation). Never use real maintainer or contributor names. The same rule applies anywhere a user identity appears — message senders, "Created by:" lines, avatar initials, participant lists, annotation labels.

### Admonitions

Use mkdocs-material admonition syntax. **Never** write raw HTML divs for callouts. The Markdown form gets the right Material icon and the tinted styling automatically.

```markdown
!!! tip "Collect the minimum"

    Mitup only stores what's needed to make the bot work. Location pins are
    discarded after the timezone lookup.
```

Types wired up (group → kinds → color):

* **Blue** (default): `note`, `info`
* **Green**: `tip`, `success`, `hint`
* **Yellow**: `warning`, `caution`, `attention`
* **Red**: `danger`, `error`, `failure`, `bug`
* **Grey**: `quote`, `example`, `question`

Guidelines:

* Always give the admonition a title (`!!! tip "Some title"`). The title carries the meaning; the type carries the color.
* Keep titles short, 2–5 words, sentence case.
* Body is normal Markdown indented 4 spaces; can contain links, lists, inline buttons, etc.
* Use `!!! warning` for anything irreversible (delete, wipe, permanent).
* Use `!!! tip` for "you can also" / "by the way" content.
* Use `!!! note` for cross-references and lifecycle / state info.
* Use `!!! quote` for citations or first-person commentary from the maintainer, or to render a verbatim bot message (see `user-guide/getting_started.md`).
