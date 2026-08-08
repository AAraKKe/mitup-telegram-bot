# Galician (gl_ES) Translation Dictionary

These rules were established by native speakers and are the **source of truth** for vocabulary
and register. They take priority over existing `.po` entries when there is a conflict.

## Core Terminology

| English | Galician | Notes |
|---|---|---|
| meeting / meetup | **quedada** | Same as Spanish. NEVER use "reunión" or "encontro" |
| public meeting | **Quedada pública** | |
| waiting list | **Listaxe de espera** | "listaxe" (Galician-normative), NOT "lista" (Castilian) |
| participants | **participantes** | |
| created by | **Creada por:** | Feminine — "quedada" is feminine |
| no limit | **Sen límite** | |
| full (status) | **Completa** | Feminine |
| full + waiting list | **Chea, listaxe de espera aberta.** | "Chea" for the standalone full status |
| settings | **Axustes** | |
| privacy | **Privacidade** | |
| the Hosts group | **o Grupo de Hosts** | Canonical name, always with the article — never "grupo exclusivo de Hosts" or the English "Hosts-Only Group" left verbatim |
| user guide | **guía de usuario** | NOT "guía de uso" |
| Host perks | **vantaxes de Host** | NEVER "privilexios". "vantaxes" is feminine — watch agreement and clitics ("mantelas", "reactivalas") |
| Host grants (admin flow) | **Vantaxes de Host** | Flow title and admin button — never "Concesións" as the flow name |
| link / unlink (Patreon account) | **vincular / desvincular** | NEVER "enlazar / desenlazar" |
| searchable | **pódese buscar** | Rephrase with the verb — never the calque "buscable" |
| open invitations (the toggle) | **Convites abertos** | It lets participants add other people; "Con convite" reads as invite-only — meaning inversion |
| invite / invitation | **convidar / convite / convidado** | NEVER "invitar / invitación / invitado" (castelanismos) |

## Brand spelling

- The product is **Mitup** / **Mitup Bot** — never "MitUp" or "MeetUp", even if the English
  source contains the typo. Copy the intended brand, not the typo.

## Write Galician, not translated English

- Translate the **meaning of the sentence in its screen context**, never word by word. If a
  literal rendering sounds like a translation ("buscable", "adicións", "debe estar no futuro"),
  rewrite it the way a native speaker would say it ("pódese buscar", "convites", "non pode
  estar no pasado").
- Never invent content that is not in the English source, and never drop content that is.

## Register

- Use informal **ti** form throughout — **no vostede**
- Prompts use the ti **imperative** ("Envíame a hora"), never the infinitive
- Never gender the user. Masculine defaults like "Benvido", "Estás conectado", "Foste
  engadido" are not acceptable; rephrase to a genderless form: "Dámosche a benvida",
  "A túa conta xa está conectada", "Engadímoste"
- The same applies to third parties named via placeholders: "Engadimos a ${name}",
  "Botamos a ${participant}", "convite de ${user}", "a persoa convidada" — never a masculine
  participle or article chain around a name ("O usuario ${name} foi engadido")
- Confirmation questions use "Seguro que queres...?" — never "Estás seguro de que queres...?"
  (gendered and heavier)

## CRITICAL: Punctuation

**Galician does NOT use inverted punctuation.** No `¡` and no `¿` — ever.

- ✅ `Estás dentro!`
- ❌ `¡Estás dentro!`
- ✅ `Seguro que queres eliminar esta quedada?`
- ❌ `¿Seguro que queres eliminar esta quedada?`

## Grammar and Vocabulary

- "quedada" is **feminine** — "nova quedada", "completa", "creada por"
- Always use Galician-normative forms, not Castilian equivalents:
  - "acó" not "aquí"
  - "só" not "solo"
  - "isto" not "esto"
  - "en canto" not "en cuanto"
  - "che gustaría" not "te gustaría"
  - "descrición" not "descripción"
  - "localización" not "ubicación"
- "amizade" (gender-neutral) for friend/friendship, not "amigo/a"
- More castelanismos to avoid: "comigo" not "conmigo", "icona" not "icono", "mesmo" not
  "incluso", "Imos" not "Vamos", "Por agora" not "Pola agora", "gratuíta" (with diaeresis)
  not "gratuita"
- **Clitic placement**: a clitic pronoun can never open a sentence. "Gustaríache envialo?",
  never "Che gustaría envialo?". After a verb, use enclisis: "Mitup notificarate", not
  "Mitup che notificará"
- **Enclitic allomorphs are Galician, not Castilian**: infinitive + feminine pronoun drops
  the -r ("compartila", "editala", "enviala", "cambiala" — never "compartirla"); a
  vowel-final imperative takes plain -a ("Reactívaa", "Establécea" — never "Reactívala")
- Verb variant: use **establecer** forms throughout (not "estabelecer") — both are valid,
  the catalog standardizes on one
- "escolle" is the canonical verb for choosing ("Escolle un idioma") — not "elixe"
- "remata / hora de remate" for ending — not "termina"

## Button Labels

| English | Galician |
|---|---|
| Join | **Unirse** |
| Leave | **Saír** |
| Create (new meeting) | **Nova quedada** |
| Edit | **Editar** |
| Delete | **Borrar** |
| Share | **Compartir** |
| Invite | **Convidar** |
| Kick out | **Botar** |
| Cancel | **Cancelar** |
| Done | **Feito** |
| Activate | **Activar** |

## Intentional Choices Worth Preserving

- Kick-out button: "Botar" — colloquial Galician for "throw out". Do not replace with
  a more formal term.
- "Se cadra logo" for "maybe later" — authentic Galician idiom. Do NOT replace with
  the Castilian "quizá después".
- "Listaxe" (not "lista") for waiting list — Galician-normative form

## Do NOT translate — fixed brand terms

These English brand terms are product identity and must appear **verbatim** in Galician — never translated, transliterated, or localized. Translate the sentence around them; keep the words themselves exactly as written:

| Term | Keep as |
|---|---|
| Host / Hosts | **Host / Hosts** |
| Brewer | **Brewer** |
| Gamemaster | **Gamemaster** |
| Commissioner | **Commissioner** |

"Host / Hosts" is the collective term for people who back the bot on Patreon; "Brewer", "Gamemaster", and "Commissioner" are the three Patreon tier names. All four appear identically on Patreon, so localizing them would break the mapping users see.

## Location concepts — two words, never one

- **lugar** = the meeting's place / the location attribute ("🗺️ Lugar", "Sen lugar definido", "o nome do lugar").
- **localización** = the map pin / coordinates (the Telegram attach feature; the menu item is "Localización").
- Never use the same word for both concepts in one string; a sentence mentioning the attribute and the pin uses lugar + localización respectively.
- **praza** = a spot/seat in a meeting. Never «lugar» for spots — lugar is the place itself.
