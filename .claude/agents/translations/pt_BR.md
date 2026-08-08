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
| the Hosts group | **o Grupo Exclusivo de Hosts** | Canonical name everywhere — never "Grupo de Hosts", "Grupo só para Hosts", or the English "Hosts-Only Group" left verbatim |
| Host perks | **vantagens de Host** | NEVER "benefícios" — one concept, one word, in Collaborate screens and notifications alike |
| Patreon account | **conta do Patreon** | Always with "do" — never the calqued apposition "conta Patreon" |
| user guide | **guia do usuário** | |
| spot (in a meeting / waiting list) | **vaga** | NOT "espaço" or "lugar" |
| location (Telegram attachment / GPS pin) | **localização** | Telegram's own pt-BR UI says "Localização" |
| place / venue (named location of a meeting) | **local** | "Nome do local"; keep "local" and "localização" distinct |
| notification lead time | **antecedência** | The notifications "Time" button/setting is minutes-before-start, not a clock time — never "Horário" there |
| schedule ahead / further out | **agendar com X dias de antecedência** | Never "dias adiante", "dias no futuro", or "agendar o quanto quiser" (reads as quantity, not horizon) |
| run/host meetings | **realizar eventos** | Never "executar" or "hospedar" eventos |
| open invitations (the toggle) | **Convites abertos** | It lets participants add other people; anything reading as invite-only is a meaning inversion |

## Brand spelling

- The product is **Mitup** / **Mitup Bot** — never "MitUp" or the legacy "MeetUp", even when the
  English source still carries the stale spelling. Fix the brand, report the English.

## Write Portuguese, not translated English

- Translate the **meaning of the sentence in its screen context**, never word by word. If a literal
  rendering sounds like a translation ("será capaz de", "de nossa parte", "sem decimais permitidos",
  "desconfigurar"), rewrite it the way a Brazilian would say it ("você pode", "do nosso lado",
  "nada de decimais", "remover").
- **No Title Case.** English headers and button labels capitalize every word; Portuguese uses
  sentence case ("Lista de convidados fechada", "Menu principal", "Data e hora").
- People **entram/saem** de um evento — never "juntar-se ao evento", which is a calque.
- "Oops" is not Portuguese — the interjection is **"Opa!"**.
- Never invent content that is not in the English source, and never drop content that is.

## Register

- Use **você** (informal but polite singular) throughout — standard Brazilian Portuguese
- Prompts use the imperative ("Envie", "Escolha"); the tap verb is **"toque em"** — never the
  calques "pressione em" / "pressionando em"
- Buttons are short: an infinitive or an imperative CTA ("Seja um Host"), never a bare "Ser ..."

## Never gender the user

Masculine participles and greetings addressed to the user are not acceptable; rephrase to a
genderless form. This includes indirect self-reference like "você mesmo".

| Gendered | Write instead |
|---|---|
| "Bem-vindo ao Mitup" | "Boas-vindas ao Mitup" |
| "Bem-vindo de volta" | "Que bom ter você de volta!" |
| "Você foi adicionado à lista de espera" | "Você entrou na lista de espera" |
| "Você foi promovido da lista de espera" | "Você saiu da lista de espera e sua participação está confirmada" |
| "Você será removido dos eventos" | "Removeremos você dos eventos" |
| "você gostaria de ser notificado" | "você quer receber a notificação" |
| "depois de você mesmo autorizar" | "depois de você ter autorizado pessoalmente" (the Patreon link-confirm anchor must keep an emphatic self-reference; tests enforce it) |

Third parties of unknown gender get the same care where a natural genderless form exists:
"<i>convite de ${user}</i>" instead of "<i>convidado por ${user}</i>"; "Adicionamos ${name}",
"Removemos ${participant}", "a pessoa convidada" instead of participle/noun chains around a name.

## Grammar

- "evento" is **masculine** — "o evento", "este evento", "um novo evento"
- "vantagens" is **feminine plural** — "suas vantagens ... desativadas", "reativá-las", "mantê-las"
- Mixed-gender referents take the masculine plural: "Toque em <b>Data</b> ou <b>Horário</b> para
  atualizá-los ... definir os dois de uma vez"
- Future subjunctive after "se/enquanto/quem/quando" in future contexts: "quem entrar", "quando ele
  estiver cheio", "enquanto sua contribuição estiver ativa", "todos que receberem o evento"
- Product names take the article: "usar **o** Mitup", "as notificações **do** Mitup"

## CRITICAL: Join and Leave Buttons

**"Aceitar" (Join) and "Recusar" (Leave) are NOT translation errors.**

They reflect a deliberate RSVP-style mental model — the user "accepts" or "declines" an
invitation, not physically "joins" or "leaves". Do not change to "Entrar"/"Sair" or any
other join/leave equivalent.

This framing extends to related strings:
- "Joined meetings" label → **"Aceitos"** (consistent with Aceitar = Join; short — it shares a keyboard row with Configurações)
- Join success message → **"Participação confirmada!"** (a confirmation, not an exclamation of presence)
- Already-joined alert → **"Você já aceitou este evento"**
- Leave-without-joining alert → **"Você não pode recusar um evento que não aceitou"**
- Joined-meetings list → **"Estes são os eventos que você aceitou"** / empty: **"Você ainda não aceitou nenhum evento"**

Body copy describing the act of joining/leaving uses **entrar/sair** ("entrou no evento",
"saiu do evento") — never "juntar-se".

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
