---
name: guards
description: Guards conventions for mitup_bot. Auto-load when writing handler validation, accessing the current user, validating callback data, or checking meeting access.
user-invocable: false
---

# Guards

Guards live in `libs/telegram/mitup_bot/guards.py`. They validate handler inputs and raise domain exceptions on failure.

<critical_rules>
  <rule>Always use guards instead of manual validation inside handlers.</rule>
</critical_rules>

## Available guards

### User access

| Function | Signature | Returns | Raises |
|----------|-----------|---------|--------|
| `current_user` | `(update, session, *, load_collections=False)` | `User` | `UserNotFound` (caught by global error handler) |
| `member_user` | `(update, session)` | `User \| None` (only when status is MEMBER) | — |
| `user_language` | `(update, session)` | `str` (lang code or fallback) | — |
| `user_registered` | `(update, session, context, alert_message)` | `User \| None` | — (answers callback query with alert) |

Use `current_user` for all handlers that require an authenticated user. Use `user_registered` only when an unauthenticated user is a valid, non-fatal case (e.g. inline query handlers). `member_user` gates `/start` routing between the member flow and re-onboarding.

### `load_collections`

`current_user` returns the user with `meetups` and `joined_links` **unloaded**. Almost every screen acts on a single meeting it resolves through `guards.meeting`, which is meeting-rooted and hands back a fully hydrated meeting — so the user's own collections are dead weight there, and the same holds for `guards.meeting_interaction_allowed`, whose every check is rooted at the meeting too.

Pass `load_collections=True` only where the handler (or something it calls or renders) actually traverses them, and put a one-line comment above the call naming what does the traversal. The traversals that exist today:

- `user.meetups` / `user.joined_links` directly — the active, past and joined meeting lists.
- `limits.at_active_meetings_cap`, via `handlers/meeting/utils.active_meetings_cap_reached` — meeting creation and reactivation.
- `user.own_meeting` — `views.meeting.keyboard_for_update` (join/leave, attach-to-chat) and the invite flow's abort branch.
- `user.joined_meeting` — the join operation's already-joined check.

Both collections are `lazy="raise"`, so a missing opt-in raises `InvalidRequestError` rather than emitting silent I/O — but only on a session-bound instance, which means the `db_test` suite catches it and `MockDbSession` unit tests do not. Decide the opt-in by reading the call graph, not by waiting for a red test.

For a handler that renders a full meeting card straight off those collections, `guards.current_user` is not enough: call `User.by_tg_user_id(..., load_collections=True, load_participants=True)` directly (see the `database` skill). The inline query is the only such caller.

All guards that take a `session` are async — `await` them.

### Message and query access

| Function | Signature | Returns | Raises |
|----------|-----------|---------|--------|
| `message` | `(update)` | `Message` | `EffectiveMessageNotSet` |
| `chat` | `(update)` | `Chat` | `EffectiveChatNotSet` |
| `callback_query` | `(update)` | `CallbackQuery` | `CallbackQueryNotSet` |
| `valid_inline_query` | `(update)` | `InlineQuery` | `InlineQueryNotSetError` |
| `chosen_inline_result` | `(update)` | `ChosenInlineResult` | `ChosenInlineResultNotSet` |

<note>`valid_callback_query` is an internal variant used by `api_wrapper.py`. Use `callback_query` in handlers.</note>

### Callback data validation

<critical_rules>
  <rule>Always pass `handler_id` to `valid_callback_data`, `valid_meeting_callback_data`, and `valid_date_callback_data` so that `MalformedCallbackData` is scoped to the correct handler in error reports.</rule>
</critical_rules>

| Function | Signature | Returns | Raises |
|----------|-----------|---------|--------|
| `valid_callback_data` | `(cb, handler_id)` | `ValidCallbackData` | `MalformedCallbackData` |
| `valid_meeting_callback_data` | `(cb, handler_id)` | `ValidMeetingCallbackData` | `MalformedCallbackData` |
| `valid_date_callback_data` | `(cb, handler_id)` | `ValidDateCallbackData` | `MalformedCallbackData` |

### Meeting access

| Function | Signature | Returns | Raises |
|----------|-----------|---------|--------|
| `meeting` | `(session, user, meeting_id, action, context, *, access=MeetingAccess.OWNER, lock=False, custom_keyboard=None, flow_context=None)` | `Meetup` | `MeetingGoneError`, `MeetingNotOwnedError`, `MeetingInactiveOwnerError` |
| `shared_meeting` | `(session, user: User \| None, meeting_id, action, update, *, lock=False, require_active=True)` | `Meetup` | `SharedMeetingGoneError`, `SharedMeetingDeniedError`, `SharedMeetingFinishedError` |
| `conversation_meeting` | `(session, user, meeting_id, action, *, lock=False, flow_context=None)` | `Meetup` | `MeetingGoneError` |
| `meeting_interaction_allowed` | `(session, user: User \| None, meeting, update)` | `bool` | — (a predicate; `shared_meeting` raises on `False`) |

All three guards raise instead of returning `None` and render nothing themselves — the global error handler owns every screen. Pick by where the caller reached the meeting from:

- **`meeting`** — the ownership-rooted surfaces the user reaches through their own bot chat.
- **`shared_meeting`** — the any-user surfaces reached by tapping a meeting card, which may sit in any chat the meeting was shared into (join, leave, attach-to-chat, the invite entry point).
- **`conversation_meeting`** — the later steps of a flow, where the id comes back from the caller's own conversation state and the entry point already decided access.

`guards.meeting` resolves the meeting through `Meetup.by_id` and returns **its own** meeting-rooted instance, whose participant leaves (owner, joined links' `user`/`invited_by`) are hydrated — so handlers render straight off the guard result and never re-load. Ownership is decided on that loaded row via `Meetup.is_owned_by`.

The guard never returns `None` and never renders: it either hands back a meeting or raises, so callers use the result directly and carry no `if meeting is None` stanza. It takes no `update` for the same reason — the exception carries what the renderer needs (see "Rejections" below).

`access` selects who gets through and which rejection stops everybody else:

| `MeetingAccess` | Lets through | Meeting not found | Inactive meeting |
|---|---|---|---|
| `OWNER` (default) | the owner of an active meeting | `MeetingGoneError` | owner → `MeetingInactiveOwnerError`; anyone else → `MeetingNotOwnedError` |
| `OWNER_OR_JOINED` | the owner, plus anyone who joined the active meeting (waiting list included) | `MeetingGoneError` | owner → `MeetingInactiveOwnerError`; anyone else → `MeetingNotOwnedError` |
| `OWNER_OR_PUBLIC` | the owner, plus anyone at all when the active meeting is public | `MeetingGoneError` | owner → `MeetingInactiveOwnerError`; anyone else → `MeetingNotOwnedError` |
| `OWNER_ANY_STATE` | the owner, whatever the meeting's state | `MeetingNotOwnedError`, as for a meeting the caller does not own | returned to the caller |

Use `OWNER` for the ownership-gated screens (every edit surface). Use `OWNER_OR_JOINED` for screens that only **display** a meeting the user can reach without owning it — e.g. the "Joined meetings" list, where the caller renders `Meetup.view_for(user)` (owner → `main_view`, non-owner → `external_view`). Use `OWNER_OR_PUBLIC` for the inline share query, the one surface where being public is itself the permission. Use `OWNER_ANY_STATE` on the surfaces that render inactive meetings themselves — the past-meetings screens, reactivation, and the delete flows — where the reactivation prompt would replace the screen the user asked for.

#### A caller without an account (`meeting`, `OWNER_OR_PUBLIC`)

Two guards accept a caller with no account; this is the first. `shared_meeting` has its own section
under "Shared surfaces", and the two share a resolution recipe: resolve the acting user with
`User.by_tg_user_id` instead of `guards.current_user`, collapse a `DELETION_REQUESTED` user to
`None` so a dying account decides nothing, and let the rejections carry
`TranslationEngine.FALLBACK_LANG`, since there is no account to read a language preference from. No
account is ever created for such a caller.

`OWNER_OR_PUBLIC` is the only profile that accepts `user=None`, and the signature enforces it: the guard is overloaded so a `User | None` argument type-checks on that profile alone, while every other profile still demands a `User`. Pass `None` when the acting Telegram user has no row — the share query resolves the sharer with `User.by_tg_user_id` instead of `guards.current_user`, since a public meeting card offers its Share button to every reader of the chat, and it collapses a `DELETION_REQUESTED` user to `None` too so a dying account shares nothing of its own.

Such a caller owns nothing: every ownership test short-circuits, so an inactive meeting and a non-public one both come back as `MeetingNotOwnedError` and the only way past the guard is the meeting's own `public` flag. A registered sharer's rejection still carries their own language, and the error handler renders the unavailable card off `error.lang` either way. `MeetingGoneError` and `MeetingNotOwnedError` take `user_db_id: int | None` and name an anonymous caller in their message; `MeetingInactiveOwnerError` keeps `int`, since only an owner can reach it.

### Rejections

Every rejection subclasses `MeetingAccessError` (itself a `GuardError`) and carries `meeting_id`, `action` and `lang`. `MeetingGoneError` and `MeetingInactiveOwnerError` also carry the back-navigation `keyboard`; the others do not, since their screens navigate on their own. The error handler owns every screen they produce. For the bot-chat rejections it picks the reply shape from the update — callback query → edit in place, message → fresh reply, inline query → the unavailable card. The `SharedMeetingError` subclasses answer on the card instead, whatever the update looks like. See the `error-handling` skill for both branches.

`action` is a short free-text description of what the user was doing. It is not user-facing: it names the attempt in the exception message and on the `Rejected meeting action` line the error handler logs for every rejection.

A non-owner is counted on the `MeetingNotOwned` metric with value `1`; the deleted-meeting and reactivation screens emit no such metric. A caller who gets through an ownership check emits the same metric with value `0`, so the series carries both outcomes.

On its success return `meeting` binds `meeting_id` as a structlog contextvar (`granted_meeting`). The bind outlives the guard, the handler and the commit, so every later line of that update — including the post-commit drain and the reconciler — names the meeting without carrying a field of its own. Only a resolved meeting is bound; a rejection carries its id on the exception instead.

Because it uses `bind_contextvars` rather than the scoped `bound_contextvars`, nothing expires it on its own: the bot's update boundary drops it (`update_trace.clear_update_scoped_state`, listed in `UPDATE_SCOPED_BINDS`). Any new bind meant to outlive a guard belongs on that list, or it will be read as the next update's.

Pass `lock=True` when the handler goes on to mutate participants or capacity: the guard then loads the meeting via `Meetup.by_id(..., for_update=True)`, acquiring the per-meeting row lock (`SELECT … FOR UPDATE` with `populate_existing`) before any capacity/waiting-list read. Read-only handlers must leave it `False`. The locked load resets the acting user's `meetups`/`joined_links` to unloaded, so a handler that both locks and opted into the collections must re-load them itself. See the `database` skill's "Per-meeting row locks" section for the full convention.

`custom_keyboard` replaces the default main-menu back button as the back-navigation row(s) in the "meeting deleted" message and the reactivation prompt — pass it when the user should return to the list they came from. It travels to the renderer on the exception, so the guard never builds a screen itself.

`flow_context` is the opt-in a mid-flow caller passes to `meeting` or `conversation_meeting`: a `MessageBase` member holding one sentence that names what the user was doing. It travels on the exception like `custom_keyboard`, and the error handler appends it to the description of the screen the rejection produces — the same single reply, one sentence longer. Pass it where the rejection interrupts a flow the screen would otherwise not mention (the invite conversation's typing step is the model); leave it out everywhere the screen already stands on its own, and those screens render exactly as they would without the parameter. The `SharedMeetingError` subclasses do not take it: what replaces a shared card is read by everybody in that chat, so it never carries a sentence about one reader's flow.

### Shared surfaces

`guards.shared_meeting` is the single guard for the surfaces a user reaches by tapping a meeting card,
whatever chat it sits in. Use it in every handler that acts on such a card (join, leave,
attach-to-chat, the invite entry point) and nowhere else. It decides three things in this order:

1. **Does the meeting resolve?** No → `SharedMeetingGoneError`. The card is stale: the error handler
   replaces it with the deleted banner and counts it on `STALE_MEETING_MESSAGE`.
2. **Does the tapped message authorize this caller?** No → `SharedMeetingDeniedError`, answered with
   the deleted-meeting alert over the untouched card and counted on `UNAUTHORIZED_MEETING_CALLBACK`.
   Decided before the meeting's state is read, so a denial discloses nothing about it.
3. **Is the meeting still active?** No → `SharedMeetingFinishedError`, which replaces the card with
   the finished banner in the *meeting's* language, since the card is what every reader of that chat
   sees. Pass `require_active=False` on the surfaces that stay useful on a finished meeting
   (attach-to-chat), which then skip this step.

`lock=True` works as it does for `meeting`, with the same consequence: the locked load resets the
acting user's `meetups`/`joined_links` to unloaded, and a handler that reads them re-loads them after
the guard returns.

Step 2 is `meeting_interaction_allowed`, available on its own as a predicate. The meeting id arrives
in client-supplied callback data, so it proves nothing: what the predicate authorizes is the *message
the tap came from*, which Telegram fills in. It allows public meetings, owners, members (waiting list
included), inviters, a tracked message of this meeting, a meeting already tracked in this chat, and a
shared card claimed by this meeting. A shared card is claimed when it is sent, by the
`chosen_inline_result` handler — an unclaimed inline message authorizes nothing, since anyone can
produce one by sharing a card of their own.

#### A caller without an account (`shared_meeting`)

The second of the two guards that accept one, on the same resolution recipe as `OWNER_OR_PUBLIC`
above — read that section first for the part both share.

`shared_meeting` accepts `user=None` only when the call passes `allow_anonymous=True`. Unlike
`meeting` it carries no access-profile to key an overload on, so the opt-in *is* the discriminator:
the overloads type-check a `User | None` on that shape alone, and join, leave and the invite entry
point keep their `User` guarantee without doing anything. The flag widens the type only — it changes
no branch inside the guard. Set it where a surface means to serve a caller with no account, which
also makes that capability visible at the call site instead of only in the signature.

The three user-rooted arms of `meeting_interaction_allowed` (owns it, has a membership, invited
somebody) short-circuit for them, leaving the meeting's `public` flag and the message-bound arms —
so an anonymous caller gets through on "you are tapping a real card this meeting claims" and nothing
else. All three `SharedMeetingError` subclasses take `user_db_id: int | None` to name them.

Attach-to-chat is the surface that relies on this — the "Make it searchable" button renders on every
card that is not already searchable, so the caller is whoever received it.
`views.meeting.keyboard_for_update` takes `User | None` for the same reason and gives an anonymous
caller the participant's keyboard, since they own nothing. Join and leave still resolve a `User`:
they register a `JOINED_ONLY` row for an unregistered caller, because joining is an act of the
person, not of the chat.

Business rules are **not** guard material and stay in the handler: capacity, waiting-list and
invitation-setting decisions (`join_allowed`, `allow_invitation`, `lock_on_start`) run on the meeting
the guard hands back.

### Conversation steps

`guards.conversation_meeting` is for the steps after a flow's entry point, where the meeting id comes
back from the caller's own conversation state and carries the authorization the entry point already
made. It re-reads the meeting and raises `MeetingGoneError` when it has been deleted **or**
deactivated while the user was typing — the same rejection for both, since neither can be written to.
No access decision is re-taken. Pass `lock=True` from the step that commits the flow's write.

## Usage pattern

```python
@HandlersRegistry.register_callback_query(MyHandlerId.SHOW, callback_data=cb.MY_CALLBACK)
@with_session
async def show(session: AsyncSession, update: Update, context: TMitupContext):
    meeting_id = guards.valid_callback_data(cb.MY_CALLBACK.parse(context.match), MyHandlerId.SHOW).id
    user = await guards.current_user(update, session)
    meeting = await guards.meeting(session, user, meeting_id, "show meeting", context)
    # ... proceed with meeting
```

A rejection aborts the handler mid-body, so anything that must happen regardless of the outcome — clearing conversation state, for instance — runs **before** the guard. A conversation handler that is aborted this way returns no state, which leaves the user in the state they were already in; that is what every other guard exception does too.

## Failure mode registration

<critical_rules>
  <rule>If a handler uses any of `current_user`, `meeting`, `valid_callback_data`, or `valid_meeting_callback_data`, it MUST be registered in `tests/bot/handlers/test_failure_modes.py` under the `CONTEXTS` list using the `Context` dataclass. Handlers whose meeting comes from conversation state declare the id the guard will resolve with the `meeting_id` field.</rule>
</critical_rules>
