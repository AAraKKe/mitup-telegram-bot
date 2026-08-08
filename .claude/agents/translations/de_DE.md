# German (de_DE) Translation Dictionary

These rules were established by native speakers and are the **source of truth** for vocabulary
and register. They take priority over existing `.po` entries when there is a conflict.

## Core Terminology

| English | German | Notes |
|---|---|---|
| meeting / meetup | **Treffen** | NEVER use "Meeting", "Verabredung", or "Ereignis" |
| public meeting | **Öffentliches Treffen** | |
| waiting list | **Warteliste** | |
| participants | **Teilnehmer** | |
| created by | **Erstellt von:** | |
| no limit | **Unbegrenzt** | NOT "kein Limit" |
| full (status) | **Voll** | |
| full + waiting list | **Voll. Warteliste ist offen** | |
| settings | **Einstellungen** | |
| privacy | **Datenschutz** | NOT "Privatsphäre" |
| the Hosts group | **Hosts-Gruppe** | Canonical name everywhere — never "Hosts-Only Group" left verbatim, never "Gruppe nur für Hosts" |
| user guide | **Handbuch** | NOT "Anleitung" or "Benutzerhandbuch" — one term everywhere, incl. link labels |
| location (Telegram attach feature / GPS pin) | **Standort** | Telegram's own German UI says "Standort"; instructions about the 📎 menu must match it |
| location (the meeting's place) | **Ort** | The place where the meeting happens; keep the Standort/Ort split consistent |
| tap (a button) | **tippe auf** | NEVER "drücke", "klicke" — everything is a phone screen |
| button | **Button** | NOT "Schaltfläche" — matches the informal du register |
| link / unlink (Patreon account) | **verbinden / trennen** | One verb pair everywhere: buttons, headlines, body text. Not "verknüpfen" |
| run / host (a meeting) | **veranstalten** | NOT "durchführen" — that is business-meeting German |
| raised limits (Host perk) | **höhere Limits** | NOT "erweiterte Limits" |
| open invitations (the toggle) | **Offene Einladungen** | It lets participants add other people; never render as invite-only ("mit Einladung") |
| set (a date/time/option, in body text) | **festlegen / einstellen** | "feststellen" means *to notice*, not *to set* — a recurring mistranslation |
| Telegram id | **Telegram-ID** | "ID" always uppercase, never "Id" |

## Brand spelling

- The product is **Mitup** / **Mitup Bot** — never "MitUp" or "MeetUp". Even if the English
  source misspells it, the German text uses the correct spelling (and the English defect gets
  reported upstream).

## Write German, not translated English

- Translate the **meaning of the sentence in its screen context**, never word by word. If a
  literal rendering sounds like a translation ("von der Warteliste befördert", "bei der
  gemeinsamen Nutzung", "neue Hinzufügungen"), rewrite it the way a native speaker would say it
  ("von der Warteliste nachgerückt", "beim Teilen", "es können keine Gäste mehr hinzugefügt
  werden").
- Use natural spoken tense: perfect, not preterite, in bot speech ("ich habe ... erwartet",
  never "ich erwartete ...").
- English interjections get their German form: "Oops!" → **"Ups!"**.
- Never invent content that is not in the English source (no added "erfolgreich"/"korrekt" in
  confirmations, no extra sentences), and never drop content that is.
- Success confirmations are short and active, following the English framing: "Treffen gelöscht.",
  "Sprache eingestellt." — not "Das Treffen wurde erfolgreich gelöscht".

## Register

- Use **du** (informal singular) throughout — **never Sie**
- Prompts use the du **imperative** ("Sende mir die Uhrzeit"), never the infinitive
- Button labels use the infinitive ("Teilnehmen", "Mitup erkunden"), per German UI convention
- Never gender the user. German predicate nouns for roles drop the article, which keeps them
  genderless — "Du bist Brewer", "Werde Host", never "Du bist ein Brewer" / "Werde ein Host"
- The same applies to third parties named via placeholders: no gendered role noun next to a
  name ("Benutzer ${name} wurde hinzugefügt" → "${name} wurde hinzugefügt"); where a noun is
  needed, use "Person" ("die eingeladene Person"). Uninflected participles ("wurde entfernt",
  "eingeladen von ${user}") are safe
- Standard German punctuation only — no inverted exclamation marks

## Grammar

- "Treffen" is **neuter** — "das Treffen" — agreements use neuter:
  "dieses Treffen", "das Treffen wurde gelöscht"
- After a colon that introduces a complete sentence, capitalize ("Tipp: Du kannst ...")
- "tippen" takes "auf": "Tippe auf den Button", never "Tippe den Button"
- Weekday abbreviations are the standard two-letter forms (Mo, Di, Mi, Do, Fr, Sa, So);
  short month for März is "Mär"
- Language names in the language picker are translated into German ("Spanisch", "Englisch", ...),
  matching the other catalogs

## Button Labels

| English | German |
|---|---|
| Join | **Teilnehmen** |
| Leave | **Verlassen** |
| Create (new meeting) | **Neues Treffen** |
| Edit | **Bearbeiten** |
| Delete | **Löschen** |
| Share | **Teilen** |
| Invite | **Einladen** |
| Kick out | **Rausschmeißen** |
| Cancel | **Abbrechen** |
| Done | **Fertig stellen** |
| Activate | **Aktivieren** |

## Intentional Choices Worth Preserving

- Kick-out button: "Rausschmeißen" — deliberately colloquial and slightly cheeky. Do not
  replace with "Entfernen" or "Herauswerfen".
- Kick-out body text: "entfernt" (neutral) — the register contrast with the button is intentional
- "Timeout" is kept as an English loanword
- "Unbegrenzt" (not "kein Limit") for no participant limit

## Do NOT translate — fixed brand terms

These English brand terms are product identity and must appear **verbatim** in German — never translated, transliterated, or localized. Translate the sentence around them; keep the words themselves exactly as written:

| Term | Keep as |
|---|---|
| Host / Hosts | **Host / Hosts** |
| Brewer | **Brewer** |
| Gamemaster | **Gamemaster** |
| Commissioner | **Commissioner** |

"Host / Hosts" is the collective term for people who back the bot on Patreon; "Brewer", "Gamemaster", and "Commissioner" are the three Patreon tier names. All four appear identically on Patreon, so localizing them would break the mapping users see.
