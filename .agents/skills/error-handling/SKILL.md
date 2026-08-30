---
name: error-handling
description: Exception hierarchy and error handler conventions. Auto-load when adding new exceptions, guard errors, or modifying error_handler.py.
user-invocable: false
---

# Error Handling

The bot uses a structured exception hierarchy combined with a centralized error handler. All exceptions are defined in `libs/core/mitup_bot/exceptions.py`; the error handler lives in `apps/bot/mitup_bot/handlers/error_handler.py`.

## Error flow

1. A handler raises an exception (usually via a guard).
2. `callback_with_metrics()` in the registry catches it and calls `error_handler.handler()`.
3. The error handler decides whether to suppress, handle specially, or classify the invocation as a genuine fault, and returns that classification as a `FaultOutcome`.
4. `callback_with_metrics()` emits the invocation's one `FAULT` sample from that outcome and flushes, in its `finally` — see the exactly-once rule in the `monitoring` skill.

Errors are **not** routed through PTB's built-in error handler — they are caught directly in the registry wrapper so that the handler's metrics context (dimensions, properties) is preserved.

`registry.process_update_error` is registered on PTB all the same, for the failures no wrapped callback owns: a routing failure, or the re-raise of an error handler that broke while answering. Without it PTB logs those itself, as an unstructured stdlib record carrying none of the update's identity.

The two planes divide the failures rather than overlapping on them. A fault that passed through an invocation is the registry's to record, line and sample both; when the update's trace already carries one, `process_update_error` skips the update whole. It writes its correlated `error` line and its `FAULT` sample only for what nothing owned.

## The unrouted-callback fallback

`registry.callback_query_fallback` catches every callback query no registered handler matched and
splits them on the wire format of the data.

A payload minted by the bot Mitup replaced is **answered, not failed**. That bot built every button's
callback data as a JSON object of exactly `{"c": <controller>, "d": "<action>:<data>"}`, and used the
bare string `nothing` for its calendar's blank cells. Neither shape is reachable from `CallbackData`
(`{action};{entity}:{id}`), so `legacy_callbacks.is_legacy_callback_data` recognises them
unambiguously; the key set is matched **exactly**, so a near-miss object stays a fault. Users are
still holding messages that bot sent, so the tap gets `CommonMessages.OLD_VERSION_MESSAGE`:

| Where the tap came from | Answer |
|---|---|
| the caller's own chat (`in_bot_chat`) | `edit_message` replaces the dead message with the notice and a main-menu button |
| anywhere else (the old bot left cards in groups) | `answer_callback_query` alert; the message is left untouched |

The language is the one on the caller's account row — most of them were migrated with one — via
`stored_lang`, falling back to the client's `language_code` as `unregistered_caller_lang` normalizes
it. The invocation closes as a completed one on `FAULT=0` and records one info line, `Answered a
legacy bot callback`, with no `callback_data` of its own: the wrapper's exit line already carries it.
Delivery is best-effort like every render in `error_handler`.

A wire form belonging to a **conversation-scoped callback** is also answered, not failed. A callback
handler registered `bindable=False` is reachable only through a conversation's state map, so its
buttons outlive their conversation in the caller's chat: a re-tap after the conversation ended (or
after a deploy wiped conversation state) arrives with no handler matching it.
`HandlersRegistry.matches_conversation_scoped_callback` recognises these by testing the data against
the patterns of the unbound registrations — the set derives from the registrations themselves, so a
new conversation button is covered without touching the fallback. The tap is answered by
`stale_conversation.answer_stale_conversation_button`: the prompt is replaced by the main menu with
`CommonMessages.STALE_BUTTONS_NOTICE` as its context line (which is what takes the unusable buttons
off the screen), the query is answered empty to clear the spinner, and the invocation closes on
`FAULT=0` with one info line, `Answered a stale conversation button` — the line the
"Mitup/Bot/Stale conversation buttons" saved query counts. Conversation prompts only exist in the
bot's own chat, so there is no group branch; language resolution and best-effort delivery follow the
legacy path above.

Anything else raises `UnboundCallbackError`. Every *globally bound* wire form this bot renders is
covered by a handler and the conversation-scoped ones by the branch above, so data that matched
nothing is a button shipped without a handler or a forged payload — a genuine fault, answered by the
generic redirect and counted `FAULT=1`.

## Exception categories

### Guard exceptions

Raised by functions in `guards.py` when handler inputs are invalid. These are the most common exceptions:

| Exception | Guard | Meaning |
|-----------|-------|---------|
| `UserNotFound` | `current_user()` | Telegram user not in the database |
| `MeetupNotFound` | `Meetup.by_id(must_exist=True)` | Meeting ID doesn't exist (raised directly in edit handlers, not from a guard) |
| `MalformedCallbackData` | `valid_callback_data()`, `valid_meeting_callback_data()` | Callback data missing required `id` |
| `MeetingGoneError` | `meeting()`, `conversation_meeting()` | The addressed meeting does not resolve to a row |
| `MeetingNotOwnedError` | `meeting()` | The caller holds none of the access the requested profile lets through |
| `MeetingInactiveOwnerError` | `meeting()` | The owner addressed a meeting of theirs that is no longer active |
| `SharedMeetingGoneError` | `shared_meeting()` | The meeting behind the tapped card no longer exists |
| `SharedMeetingDeniedError` | `shared_meeting()` | The tapped message gives the caller no claim on that meeting |
| `SharedMeetingFinishedError` | `shared_meeting()` | The meeting behind the tapped card is no longer active |
| `EffectiveUserNotSet` | `current_user()` | Telegram update has no `effective_user` |
| `EffectiveChatNotSet` | `chat()` | Telegram update has no `effective_chat` |
| `EffectiveMessageNotSet` | `message()` | Telegram update has no `effective_message` |
| `CallbackQueryNotSet` | `callback_query()` | Update has no callback query |

### Context exceptions

| Exception | Meaning |
|-----------|---------|
| `ContextPropertyNotSetError` | User data property (meeting ID, text) not found in context registry |
| `ContextPropertyConversionError` | Stored value can't be converted to expected type |
| `InvalidUserData` | `user_data` is `None` (should never happen in practice) |

### Telegram interaction exceptions

| Exception | Meaning |
|-----------|---------|
| `InactiveUserInteraction` | A blocked/deleted user interacted with the bot |
| `CallbackQueryTextTooLong` | Callback query answer text exceeds 200 chars |
| `NoMessageAvailable` | Cannot edit — neither `message_id` nor `inline_message_id` present |

### Registration exceptions

| Exception | Meaning |
|-----------|---------|
| `HandlerRegisteredError` | Duplicate `HandlerId` in the registry |
| `HandlerNotRegistered` | Referenced handler not found during conversation handler composition |
| `WrongCommandNameError` | Command handler function doesn't follow naming convention |

## The error handler

`error_handler.handler()` in `apps/bot/mitup_bot/handlers/error_handler.py` is the entry point for all caught exceptions.

### Suppressed errors

`SUPPRESSED_EXCEPTIONS` maps exception types to sets of known-harmless message strings; `should_ignore_error()` checks this mapping and suppresses silently:

```python
SUPPRESSED_EXCEPTIONS = {
    BadRequest: {"Message to edit not found"},
}
```

<note>
Two suppression mechanisms exist at different layers — use the correct one:

- **`SUPPRESSED_EXCEPTIONS`** (global, `error_handler.py`): for Telegram API errors that can arise anywhere and should be silently ignored across all handlers.
- **`EDIT_MESSAGE_ERRORS_TO_IGNORE_PATTERNS`** (inline, `api_wrapper.py`): for errors specific to `edit_message` calls (e.g. "Message is not modified") — caught before they reach the global error handler.

Never add try/except blocks in handlers for either case.
</note>

### Inactive user handling

`InactiveUserInteraction` with `private=True` triggers `handle_inactive_user()`, which transitions the user's `status` from `MEMBER` to `LEFT` via `User.mark_inactive()` and emits `INACTIVE_USER_SET`. This happens when:
- A user has blocked the bot (raises `Forbidden`)
- A user's account is deleted (raises `BadRequest` with "not found")

The `private` flag distinguishes private chat errors (where we should mark inactive) from group chat errors (where we should not). The `TelegramApi` methods in `api_wrapper.py` raise `InactiveUserInteraction` with the appropriate `private` value.

### Missing account handling

`UserNotFound` splits on **where the caller is standing**, because the friendly answer only works on one surface. `in_bot_chat()` is the private-chat predicate that split reads, shared with `shared_banner_keyboard()`; an update carrying no chat at all (an inline query, or a callback on an inline message, which sends only its `inline_message_id`) counts as "not the bot's chat", and so does an invocation with no update.

The class is matched exactly on both paths: every other `GuardError` subclass outside the `MeetingAccessError` tree is a genuine fault wherever it came from.

#### In the caller's own chat with the bot — an expected business state

A row that no longer resolves is the normal end of the data-deletion and user-cleanup runs, not a code fault, and every button its owner is still holding goes through it. `handle_user_not_found()` answers it, the branch classifies the invocation as `FAULT=0`, and it mints no counter of its own — the occurrence is recorded on the log plane, under the constant event name `Rejected interaction from unregistered user` with `reason="user_row_not_found"`.

A callback query has its screen replaced by `CommonMessages.ACCOUNT_NOT_FOUND` with **no keyboard** — every button on the replaced screen resolves through the missing row and would be answered with this same notice, so the only way forward is the `/start` the text names. Any other update in that chat gets the notice as a fresh reply.

#### Anywhere else — a genuine fault

Every other surface answers an unregistered caller through its own `guards.user_registered`, with the per-surface alert its call site chose, **before** any code that can raise `UserNotFound` runs. One reaching the error handler from a card tapped in a group, a card shared through inline mode, an inline query or a group message therefore means that path shipped unguarded. It falls through to the fault classification: `FAULT=1` carrying `error_type`, written to the single `log.error` site every fault shares. The `/start` notice would be wrong there regardless — the caller is not in the bot's chat, so the command they were told to send would land in the group.

The generic `notify_guard_error` redirect does **not** run for these: there is no account to build a main menu for and no chat of ours to post one into. `answer_unregistered_caller()` answers in the shape the surface can carry instead:

| Update | Answer |
|---|---|
| callback query | `answer_callback_query` alert with `CommonMessages.UNEXPECTED_ERROR_ALERT`; the card itself is left alone |
| inline query | `answer_inline_query` with no results |
| any other update | nothing at all — every delivery would post into somebody else's chat |

The stored language went with the row on both paths, so `unregistered_caller_lang()` renders whichever message is used in the locale `locale_for_language_code()` maps the client's `language_code` onto — the same normalization registration uses.

Delivery is best-effort on both paths, as everywhere else in this module.

### Meeting guard rejections

Every `MeetingAccessError` subclass (see the table above) is answered by `handle_meeting_access_error()` and never reaches the fault metrics. Each one stands for a screen, and the exception carries everything the screen needs — `meeting_id`, `action`, `lang`, the back-navigation `keyboard`, and the optional `flow_context` — so no DB round-trip happens here. The guards render nothing themselves.

A `flow_context` (see the `guards` skill) is rendered by `meeting_access_view()` as one extra sentence at the end of the screen's description. It never changes the reply shape: still one reply, in whichever shape the table below picks. A rejection that carries none renders the screen exactly as its reason defines it.

For the bot-chat rejections the reply shape comes from the update, not from the exception:

| Update | Reply |
|---|---|
| callback query | `edit_message` — the screen the button sits on is replaced |
| message | `send_message` — there is no message of ours to replace |
| inline query | `answer_inline_query` with `meeting.unavailable_inline_view` |

The `SharedMeetingError` subclasses come from a tap on a meeting card that may sit in any chat, so they answer on the card whatever the update looks like, in `deliver_shared_meeting_answer()`:

| Rejection | Reply | Counter |
|---|---|---|
| `SharedMeetingGoneError` | `edit_message` with the deleted banner — the card is out of date | `STALE_MEETING_MESSAGE` |
| `SharedMeetingFinishedError` | `edit_message` with the finished banner, in the meeting's language | — |
| `SharedMeetingDeniedError` | `answer_callback_query` alert with the deleted-meeting copy; the card is left alone | `UNAUTHORIZED_MEETING_CALLBACK` |

`SHARED_MEETING_METRICS` maps the rejection type to its counter; a rejection absent from it emits none.

The two banner edits take their keyboard from `shared_banner_keyboard()`: in the bot's own chat the banner is the whole screen the user is left on, so it carries the main-menu row; in a group, a supergroup, a channel, or an inline message (which carries no chat at all) the banner replaces the card in place with no keyboard. The denial is unaffected — the card is never touched.

Acting on a meeting that is gone, inactive or somebody else's is what a stale button produces, not a code fault, so the branch classifies the invocation as `FAULT=0` — the interaction is counted as a completed one, exactly like a handler that ran to the end. `MeetingNotOwnedError` additionally emits `MeetingNotOwned` with value `1`; no other rejection carries a counter of its own.

All six are recorded under one constant event name, `Rejected meeting action`, with `MEETING_REJECTION_REASONS` supplying the `reason` that tells them apart — an interpolated message would make the `event` value itself variable and the rejections uncountable. The level splits them: the four that mean somebody tapped a button that should not have worked are warnings, while `MeetingInactiveOwnerError` and `SharedMeetingFinishedError` (listed in `MEETING_REJECTIONS_AT_INFO`) are screens the system produces on purpose and stay at info. A new subclass must be added to the reason map — `test_every_meeting_rejection_has_a_reason` fails otherwise.

Delivery is best-effort, like every other render in this module: an exception raised while answering has no handler left above it and would reach `process_update` as a second, unhandled fault.

### Fault metrics

For all other (unexpected) errors:
1. One dimensionless `FAULT` metric is emitted for aggregate monitoring, carrying the exception's qualified class name in an `error_type` EMF property
2. One `log.error` line carries the traceback, the `error_type`, the trigger that produced it (`fault_fields_from_update`, as `update_payload`) and — when the failure happened inside a write-mode critical section — the `phase`/`committed` pair from `db.current_write_state()`, under the handler's bound contextvars
3. In `DEV` mode, the exception is logged with Rich formatting

`phase`/`committed` are what separate "the user's action failed" from "it landed and the render is stale": `begin_write` marks `BODY` on entry and `POST_COMMIT_FAN_OUT` once the transaction has committed, and leaves the mark behind when the section exits through an exception so the error handler can still read it. A read-only handler is in no critical section and its fault line carries neither field.

Bounding that mark to the work it describes belongs to whoever owns the boundary — for the bot, the update boundary, via `db.clear_write_state()` in `update_trace.clear_update_scoped_state`. Any other caller of `begin_write` (the events jobs drive it directly) owns the same responsibility if it ever reads the mark.

The exception class is a property, never part of the metric name: a name minted from a runtime value opens a separately-billed CloudWatch series per class that nothing charts or alarms on. It travels to the caller on the returned `FaultOutcome` rather than on an emission of this module's own — see the exactly-once rule in the `monitoring` skill.

## The `handle_edit_errors` context manager

`api_wrapper.py` provides `handle_edit_errors()` for safe message editing:

```python
async with handle_edit_errors(adapter=self.adapter):
    await self.adapter.bot.edit_message_text(...)
```

It handles two cases:
- **Message not modified** (content unchanged) — silently ignored via `EDIT_MESSAGE_ERRORS_TO_IGNORE_PATTERNS`
- **Message not found** (deleted by user) — emits `MESSAGE_DELETED`

All `edit_message` calls in `TelegramApi` already use this. Do not add custom try/except blocks for these errors.

## Adding new exceptions

1. Define the exception in `libs/core/mitup_bot/exceptions.py`.
2. Include contextual data (user IDs, handler IDs, callback data) in the constructor — this aids debugging.
3. If the exception should be suppressed, add it to `SUPPRESSED_EXCEPTIONS` in the error handler.
4. If the exception needs special handling (like `InactiveUserInteraction`), add a branch in `error_handler.handler()`.
5. If guards raise the new exception, register affected handlers in `tests/bot/handlers/test_failure_modes.py` (see the `test-conventions` skill's `references/failure-modes.md`).
