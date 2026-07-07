# Brazilian Portuguese (pt_BR) Translation Dictionary

These rules were established by native speakers and are the **source of truth** for vocabulary
and register. They take priority over existing `.po` entries when there is a conflict.

## Core Terminology

| English | Portuguese (BR) | Notes |
|---|---|---|
| meeting / meetup | **evento** | NEVER use "reunião" or "encontro" |
| public meeting | **Evento público** | |
| waiting list | **Lista de espera** | Use "Fila de espera" only in the full+waiting status string |
| participants | **participantes** | |
| created by | **Criado por:** | |
| no limit | **Sem limite** | |
| full (status) | **Cheio** | |
| full + waiting list | **Lotado. Fila de espera aberta** | "Lotado" (sold-out/packed), more vivid than just "Cheio" |
| settings | **Configurações** | |
| privacy | **Privacidade** | |

## Register

- Use **você** (informal but polite singular) throughout — standard Brazilian Portuguese

## Grammar

- "evento" is **masculine** — "o evento", "este evento", "um novo evento"

## CRITICAL: Join and Leave Buttons

**"Aceitar" (Join) and "Recusar" (Leave) are NOT translation errors.**

They reflect a deliberate RSVP-style mental model — the user "accepts" or "declines" an
invitation, not physically "joins" or "leaves". Do not change to "Entrar"/"Sair" or any
other join/leave equivalent.

This framing extends to related strings:
- "Joined meetings" label → **"Eventos aceitos"** (consistent with Aceitar = Join)
- Join success message → **"Participação confirmada!"** (a confirmation, not an exclamation of presence)

## Button Labels

| English | Portuguese (BR) |
|---|---|
| Join | **Aceitar** |
| Leave | **Recusar** |
| Create (new meeting) | **Novo evento** |
| Edit | **Editar** |
| Delete | **Excluir** |
| Share | **Compartilhar** |
| Invite | **Convidar** |
| Kick out | **Remover** |
| Cancel | **Cancelar** |
| Done | **Concluído** |
| Activate | **Ativar** |

## Intentional Choices Worth Preserving

- Delete: **"Excluir"** — NOT "deletar" or "apagar"
- Full + waiting: **"Lotado"** (sold-out/packed) rather than just "Cheio" — more expressive
- Time label: **"Horário"** (scheduled time slot), NOT "hora"
- Join success: **"Participação confirmada!"** — a deliberate confirmation framing,
  different from other languages by design

## Do NOT translate — fixed brand terms

These English brand terms are product identity and must appear **verbatim** in Portuguese — never translated, transliterated, or localized. Translate the sentence around them; keep the words themselves exactly as written:

| Term | Keep as |
|---|---|
| Host / Hosts | **Host / Hosts** |
| Brewer | **Brewer** |
| Gamemaster | **Gamemaster** |
| Commissioner | **Commissioner** |

"Host / Hosts" is the collective term for people who back the bot on Patreon; "Brewer", "Gamemaster", and "Commissioner" are the three Patreon tier names. All four appear identically on Patreon, so localizing them would break the mapping users see.
