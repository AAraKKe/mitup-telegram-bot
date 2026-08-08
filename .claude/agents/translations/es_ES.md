# Spanish (es_ES) Translation Dictionary

These rules were established by native speakers and are the **source of truth** for vocabulary
and register. They take priority over existing `.po` entries when there is a conflict.

## Core Terminology

| English | Spanish | Notes |
|---|---|---|
| meeting / meetup | **quedada** | NEVER use "reunión", "encuentro", or "evento" |
| public meeting | **quedada pública** | |
| waiting list | **lista de espera** | |
| participants | **participantes** | |
| created by | **Creada por:** | Feminine — "quedada" is feminine |
| no limit | **Sin límite** | |
| full (status) | **Completa** | Feminine to agree with "quedada" |
| full + waiting list | **Completa con lista de espera** | |
| settings | **Ajustes** | NOT "Configuración" |
| privacy | **Privacidad** | |
| members (of a meeting) | **participantes** | NEVER "miembros" for the people in a quedada |
| the Hosts group | **el Grupo de Hosts** | Canonical name, always with the article — never "Grupo solo para Hosts", "Grupo para Hosts", or the English "Hosts-Only Group" left verbatim |
| user guide | **guía de usuario** | NOT "guía de uso" |
| Host grants (admin flow) | **Ventajas de Host** | NEVER "concesiones" in user-visible copy |
| searchable | **se puede buscar** | Rephrase with the verb — never the calque "buscable" |
| a spot / free place in a meeting | **plaza / hueco** | Never "lugar" or "espacio" for spots — "lugar" is the venue |
| lead time (notifications, scheduling horizon) | **antelación** | "con más antelación", button "⏰ Antelación" — never the spatial calque "programar más lejos" |
| the meeting's location (the attribute holding name + pin) | **localización** | The umbrella concept: "Una quedada puede tener una localización asociada" |
| the map pin / coordinates (Telegram attach feature) | **ubicación** | The pin sent in a message; Telegram's Spanish UI item is "Ubicación" |
| the place / venue (casual references, its name) | **lugar** | "el nombre del lugar", button "🗺️ Lugar" |
| open invitations (the toggle) | **Invitaciones abiertas** | It lets participants add other people; "Con invitación" reads as invite-only — meaning inversion |

## Brand spelling

- The product is **Mitup** / **Mitup Bot** — never "MitUp". Copy the casing from the English source.

## Write Spanish, not translated English

- Translate the **meaning of the sentence in its screen context**, never word by word. If a literal
  rendering sounds like a translation ("buscable", "adición", "debe ser en el futuro"), rewrite it
  the way a native speaker would say it ("se puede buscar", "invitación", "tiene que ser una hora futura").
- Never invent content that is not in the English source, and never drop content that is.
- A success confirmation tells the user what they will experience, not that a value was stored —
  follow the English source's framing.

## Register

- Use **tú** (informal singular) throughout — **never usted** ("Selecciona", never "Seleccione")
- Prompts use the tú **imperative** ("Envíame la hora"), never the infinitive ("Enviarme la hora")
- Never gender the user. Masculine defaults like "Bienvenido", "conectado", "listo" are not
  acceptable; rephrase to a genderless form: "Te damos la bienvenida", "Tu cuenta ya está
  conectada", "Todo listo"
- The same applies to **third parties named via placeholders** (`${name}`, `${user}`,
  `${participant}`): no masculine participle or article chains around a name. Restructure:
  "Se ha añadido a ${name}", "Se ha expulsado a ${participant}", "invitación de ${user}",
  "la persona invitada" — never "El usuario ${name} ha sido añadido"

## Punctuation

- Exclamations always open with inverted mark: `¡Estás dentro!`
- Questions always open with inverted mark: `¿Estás seguro?`

## Grammar

- "quedada" is **feminine** — all agreeing adjectives and past participles must be feminine:
  "completa", "pública", "creada por", "nueva quedada"
- "participante/participantes" is gender-neutral
- Question words keep their accent in indirect questions: "Dime cuál será el título",
  "elige a quién quieres expulsar"
- Pronouns and articles must agree in number with their referent: "reenviar tus quedadas a
  quienes **las** reciban" (not "la")

## Button Labels

| English | Spanish |
|---|---|
| Join | **Unirse** |
| Leave | **Salirse** |
| Create (new meeting) | **Nueva quedada** |
| Edit | **Editar** |
| Delete | **Borrar** |
| Share | **Compartir** |
| Invite | **Invitar** |
| Kick out | **Expulsar** |
| Cancel | **Cancelar** |
| Done | **Hecho** |
| Activate | **Activar** |

## Intentional Choices Worth Preserving

- Kick-out **button**: "Expulsar" (formal imperative)
- Kick-out **body text**: "echar" / "echado/a" (colloquial) — intentional register contrast
  between button and body copy
- "Ajustes" for settings (not "Configuración")

## Do NOT translate — fixed brand terms

These English brand terms are product identity and must appear **verbatim** in Spanish — never translated, transliterated, or localized. Translate the sentence around them; keep the words themselves exactly as written:

| Term | Keep as |
|---|---|
| Host / Hosts | **Host / Hosts** |
| Brewer | **Brewer** |
| Gamemaster | **Gamemaster** |
| Commissioner | **Commissioner** |

"Host / Hosts" is the collective term for people who back the bot on Patreon; "Brewer", "Gamemaster", and "Commissioner" are the three Patreon tier names. All four appear identically on Patreon, so localizing them would break the mapping users see.
