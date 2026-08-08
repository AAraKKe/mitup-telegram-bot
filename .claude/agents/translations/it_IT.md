# Italian (it_IT) Translation Dictionary

These rules were established by native speakers and are the **source of truth** for vocabulary
and register. They take priority over existing `.po` entries when there is a conflict.

## Core Terminology

| English | Italian | Notes |
|---|---|---|
| meeting / meetup | **incontro** | NEVER use "riunione", "evento", or "meeting" |
| public meeting | **Incontro pubblico** | |
| waiting list | **Lista d'attesa** | Note the apostrophe: "d'attesa", not "di attesa" |
| participants | **partecipanti** | |
| created by | **Creato da:** | Masculine — "incontro" is masculine |
| no limit | **Senza limiti** | Lit. "without limits" |
| full (status) | **Pieno** | Masculine to agree with "incontro" |
| full + waiting list | **Pieno, lista d'attesa aperta** | |
| settings | **Impostazioni** | |
| privacy | **Privacy** | Kept as English loanword — intentional |
| members (of a meeting) | **partecipanti** | NEVER "membri" for the people in an incontro; "utenti" for registered bot users |
| participants list | **elenco dei partecipanti** | One rendering — never mix with "lista dei partecipanti" |
| the Hosts group | **il Gruppo Hosts** | Canonical name everywhere — never the English "Hosts-Only Group" left verbatim |
| location (Telegram feature / GPS share) | **posizione** | The Telegram menu item is capitalized: "scegli Posizione" |
| location (the meeting place) | **luogo** | "Nome del luogo", "Nessun luogo definito" — never "posizione" for the place itself |
| schedule further ahead | **pianificare con più anticipo** | Never the spatial calque "pianificare più lontano" |
| support (verb) | **sostenere** | "sostenere Mitup", "sostenerlo" — not "supportare" |
| in N days (future point) | **tra ${n} giorni** | "in ${n} giorni" means duration, not a future date |
| the button below | **il pulsante qui sotto** | Not "il pulsante di seguito" |
| invited by (guest tag) | **ospite di ${user}** | Genderless; "invitato da" genders the guest |

## Brand spelling

- The product is **Mitup** / **Mitup Bot** — never "MitUp" or "MeetUp", even where the English
  source itself is wrong. Copy the correct casing, report the English defect.

## Write Italian, not translated English

- Translate the **meaning of the sentence in its screen context**, never word by word. If a literal
  rendering sounds like a translation ("i nuovi aggiunti non sono più consentiti", "essere
  notificato", "una coppia di fratelli", "Nessun incontro è stato condiviso in questa chat ancora"),
  rewrite it the way a native speaker would say it ("non si possono più aggiungere persone",
  "ricevere la notifica", "due fratelli", "Nessun incontro è stato ancora condiviso in questa chat").
- Button names quoted in body text must match the actual Italian button label: the Done button is
  **Fatto**, so "tap Done" is "tocca Fatto", never "tocca Fine".
- "Free meetings" means meetings on the free plan — "Con il piano gratuito", never "gli incontri
  gratuiti" (which reads as meetings that cost nothing to attend).
- Never invent content that is not in the English source, and never drop content that is.

## Register

- Use **tu** (informal singular) throughout — **never Lei**
- Prompts use the tu **imperative** ("Inviami l'ora"), never the infinitive
- Never gender the user. Masculine participles and adjectives addressing the user are not
  acceptable: "Benvenuto", "Ti sei unito", "Sei stato aggiunto", "Sei sicuro?", "tu stesso",
  "Bentornato", "sei il benvenuto". Rephrase genderless: "Ti diamo il benvenuto", "Sei dentro!",
  "Ora sei in lista d'attesa", "Vuoi davvero...?", "personalmente", "È bello riaverti qui",
  "il Gruppo Hosts ti aspetta". Verbs with *avere* and future/present forms are safe
  ("Hai cacciato", "Ti rimuoveremo", "Partecipi già"). The same applies to third parties
  named via placeholders: "Abbiamo aggiunto ${name}", "ospite di ${user}" — never a
  masculine participle or article chain around a name.
- Meeting participants other than the user may also be women — never emit a bare masculine
  participle after a name placeholder ("${participant} cacciato"); use an *avere* construction
  ("Hai cacciato ${participant}", "Abbiamo aggiunto ${name}").

## Grammar

- "incontro" is **masculine** — "l'incontro", "questo incontro", "il tuo incontro"
- All adjectives and past participles must agree: "pieno", "eliminato", "creato"
- Clitic pronouns must agree with their referent: "la descrizione... Vuoi inviarla?" (not "inviarlo")
- Status labels agree with what they describe: notifications are feminine plural, so
  "Notifiche: Abilitate ✅ / Disabilitate ❌"
- Partitive clitic with numbered objects: "Chiudine o eliminane uno", not "Chiudi o elimina uno"

## Button Labels

| English | Italian |
|---|---|
| Join | **Partecipa** |
| Leave | **Abbandona** |
| Create (new meeting) | **Nuovo incontro** |
| Edit | **Modifica** |
| Delete | **Elimina** |
| Share | **Condividi** |
| Invite | **Invita** |
| Kick out | **Caccia** |
| Cancel | **Annulla** |
| Done | **Fatto** |
| Activate | **Attiva** |

## Intentional Choices Worth Preserving

- Leave button: **"Abbandona"** — deliberately evocative. Do not change to "Esci" or "Lascia".
- Join button: **"Partecipa"** — imperative, consistent with the action-verb button style.
- Kick-out button: **"Caccia"** — colloquial (lit. "chase out"). Do not change to "Rimuovi".
- Time label: **"Orario"** (scheduled time slot), NOT "ora" (generic hour).
- "Privacy" and "Timeout": kept as English loanwords — common and intentional in Italian UI.

## Do NOT translate — fixed brand terms

These English brand terms are product identity and must appear **verbatim** in Italian — never translated, transliterated, or localized. Translate the sentence around them; keep the words themselves exactly as written:

| Term | Keep as |
|---|---|
| Host / Hosts | **Host / Hosts** |
| Brewer | **Brewer** |
| Gamemaster | **Gamemaster** |
| Commissioner | **Commissioner** |

"Host / Hosts" is the collective term for people who back the bot on Patreon; "Brewer", "Gamemaster", and "Commissioner" are the three Patreon tier names. All four appear identically on Patreon, so localizing them would break the mapping users see.
