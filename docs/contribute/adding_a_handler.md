---
icon: material/plus-box-outline
---

# Adding a handler

A handler is an async function that runs when a matching Telegram update arrives: a `/command`, a button press, a text reply, an inline query. Adding one is the most common first contribution, so this page walks the whole path from an empty file to a passing test.

The code lives under [`mitup_bot/handlers/`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/mitup_bot/handlers), one package per feature. A package like [`main_menu/`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/mitup_bot/handlers/main_menu) is a good one to read alongside this page. It holds an `enums.py`, one module per screen, and an `__init__.py` that wires them together.

!!! danger "Two rules that never bend"

    * No hardcoded user-facing text. Every string a user sees resolves through a `MessageBase` member so it can be translated. Not even error alerts are exceptions.
    * Never hardcode the language. Pass `lang=user.lang` (or `meeting.lang`) to every `.get()` call. A literal `lang="en"` is a bug the reviewer will catch.

## Define a HandlerId

Every handler is identified by a `HandlerId` enum member. Each feature package owns a subclass in its `enums.py`. The members are plain `auto()` values; the base class in [`mitup_bot/handler_id.py`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/mitup_bot/handler_id.py) derives the string value and the metrics dimension for you.

```python
from enum import auto

from mitup_bot.handler_id import HandlerId


class RemindersHandlerId(HandlerId):
    SHOW_REMINDERS_CALLBACK = auto()
```

Name callback members with a `_CALLBACK` suffix so the intent reads at a glance, and add a new subclass rather than borrowing another feature's enum.

## Register the callback

Decorate the function with the matching `HandlersRegistry` method. The registry in [`registry.py`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/mitup_bot/handlers/registry.py) has one per update type:

| Update | Method |
|--------|--------|
| `/command` | `register_command` |
| Button press | `register_callback_query` |
| Text or media message | `register_message` |
| Inline query | `register_inline_handler` |
| Bot blocked / unblocked | `register_chat_member` |
| Multi-step flow | `register_conversation_handler` |

The function name follows the registration type: `callback_query_<action>` for a button, `command_<name>` for a command, `<action>_message_handler` for a message. The `callback_query_` prefix is required, not a style preference.

```python
@HandlersRegistry.register_callback_query(
    RemindersHandlerId.SHOW_REMINDERS_CALLBACK,
    callback_data=cb.SHOW_REMINDERS,
)
```

Button actions carry their own `CallbackData` instance, defined once in [`mitup_bot/utils/callbacks.py`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/mitup_bot/utils/callbacks.py) and reused at every call site.

## Wrap database access

If the handler touches the database, add `@with_session` from [`mitup_bot/db.py`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/mitup_bot/db.py) directly under the registration decorator. It injects an `AsyncSession` as the first positional argument.

```python
@HandlersRegistry.register_callback_query(
    RemindersHandlerId.SHOW_REMINDERS_CALLBACK,
    callback_data=cb.SHOW_REMINDERS,
)
@with_session
async def callback_query_show_reminders(
    session: AsyncSession, update: Update, context: TMitupContext
) -> None:
    ...
```

A handler that mutates state and then fans out over Telegram (it takes the per-meeting row lock, refreshes meeting messages, or notifies participants) uses `@with_session(write=True)` instead. The [`database`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/database/SKILL.md) skill covers when and why.

## Validate the input with guards

Handlers never trust the raw update. The [`guards`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/mitup_bot/guards.py) module turns a fuzzy `Update` into the concrete objects you need, raising a clean error when something is missing. Fetch the user early, before branching on anything else, so `user.lang` is in scope everywhere below.

```python
callback_data = guards.valid_callback_data(
    cb.SHOW_REMINDERS.parse(context.match), RemindersHandlerId.SHOW_REMINDERS_CALLBACK
)
user = await guards.current_user(update, session)
```

## Build the view and the text

The reply is a view, not a raw message. Check the factory catalogue in [`views/factory.py`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/mitup_bot/views/factory.py) before building one by hand; something like `confirmation_view` or `main_menu_view` often already fits. Every factory takes a `RenderContext` as its first argument; build it in the handler with `guards.render_context(user, update, context)`. Button labels and the description come from `MessageBase` subclasses in [`mitup_bot/utils/messages.py`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/mitup_bot/utils/messages.py), rendered with `.get(lang=user.lang)`.

```python
view = MitupView(
    description=ReminderMessages.LIST.get(lang=user.lang),
    keyboard=[
        [ButtonConfig(text=ButtonMessages.MAIN_MENU.get(lang=user.lang), callback_data=cb.MAIN_MENU)],
    ],
)
```

If your reply needs a string that does not exist yet, add it to the right `MessageBase` subclass, run `hatch run dev:update-locales`, and hand the new keys to the translator. The [`user-facing-text`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/user-facing-text/SKILL.md) skill owns the wording and the `MessageBase` API.

## Wire it into the package

A handler only runs once its module is imported, because the import is what executes the decorator that registers it. Import the new module from the package `__init__.py`, and import that package from [`mitup_bot/handlers/__init__.py`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/mitup_bot/handlers/__init__.py).

Order matters in two places:

* A conversation handler looks up its entry points and states at registration time, so those handlers must be imported before the module that composes them.
* Global catch-all handlers are imported last, so the specific handlers claim their updates first.

When two modules need each other's enums, pull the shared enum into a standalone file rather than importing across handler modules. Cross-importing handler logic is not allowed; shared helpers go in a `utils.py` inside the same package.

## Add a test

Every handler gets a test at `tests/handlers/<package>/test_<module>.py`. If the handler calls any `guards.*` function, also register its context in `tests/handlers/test_failure_modes.py`; that matrix exercises each guard's failure path and turns red if a new guard call is left out. Run the ones you touched as you go:

```bash
hatch run dev:test-hook tests/handlers/reminders/
```

Before opening a merge request, run the full gate:

```bash
hatch run dev:validate
```

## Where to go deeper

This page is the map. Two skills hold the detail:

* [`handler-conventions`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/handler-conventions/SKILL.md) for the full rules on registration, conversation state machines, filters, callback-data formats, and the add/remove checklists.
* [`new-handler`](https://gitlab.com/meetupbot/mitup-telegram-bot/-/blob/main/.agents/skills/new-handler/SKILL.md) for scaffolding a fresh package with the files already in the right shape.
