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
| `meeting` | `(session, user, meeting_id, action, context, *, access=MeetingAccess.OWNER, lock=False, custom_keyboard=None)` | `Meetup` | `MeetingGoneError`, `MeetingNotOwnedError`, `MeetingInactiveOwnerError` (all rendered by the global error handler) |
| `meeting_interaction_allowed` | `(session, user, meeting, update, context)` | `bool` | — (answers with the deleted-meeting alert) |

`guards.meeting` is the single meeting guard for the bot chat (not inline). It resolves the meeting through `Meetup.by_id` and returns **its own** meeting-rooted instance, whose participant leaves (owner, joined links' `user`/`invited_by`) are hydrated — so handlers render straight off the guard result and never re-load. Ownership is decided on that loaded row via `Meetup.is_owned_by`.

The guard never returns `None` and never renders: it either hands back a meeting or raises, so callers use the result directly and carry no `if meeting is None` stanza. It takes no `update` for the same reason — the exception carries what the renderer needs (see "Rejections" below).

`access` selects who gets through and which rejection stops everybody else:

| `MeetingAccess` | Lets through | Meeting not found | Inactive meeting |
|---|---|---|---|
| `OWNER` (default) | the owner of an active meeting | `MeetingGoneError` | owner → `MeetingInactiveOwnerError`; anyone else → `MeetingNotOwnedError` |
| `OWNER_OR_JOINED` | the owner, plus anyone who joined the active meeting (waiting list included) | `MeetingGoneError` | owner → `MeetingInactiveOwnerError`; anyone else → `MeetingNotOwnedError` |
| `OWNER_ANY_STATE` | the owner, whatever the meeting's state | `MeetingNotOwnedError`, as for a meeting the caller does not own | returned to the caller |

Use `OWNER` for the ownership-gated screens (every edit surface). Use `OWNER_OR_JOINED` for screens that only **display** a meeting the user can reach without owning it — e.g. the "Joined meetings" list, where the caller renders `Meetup.view_for(user)` (owner → `main_view`, non-owner → `external_view`). Use `OWNER_ANY_STATE` on the surfaces that render inactive meetings themselves — the past-meetings screens, reactivation, and the delete flows — where the reactivation prompt would replace the screen the user asked for.

### Rejections

The three rejections all subclass `MeetingAccessError` (itself a `GuardError`) and carry `meeting_id`, `action` and `lang`. `MeetingGoneError` and `MeetingInactiveOwnerError` also carry the back-navigation `keyboard`; `MeetingNotOwnedError` does not, since its screen is the main menu, which navigates on its own. The error handler owns every screen they produce and picks the reply shape from the update — callback query → edit in place, message → fresh reply, inline query → the unavailable card. See the `error-handling` skill for that branch.

`action` is a short free-text description of what the user was doing. It is not user-facing: it names the attempt in the exception message and in the warning line the error handler logs.

A non-owner is logged and counted on the `MeetingNotOwned` error metric; the deleted-meeting and reactivation screens emit no such metric, and the reactivation prompt is not logged at all. A caller who gets through an ownership check emits the same metric with value `0`, so the series carries both outcomes.

Pass `lock=True` when the handler goes on to mutate participants or capacity: the guard then loads the meeting via `Meetup.by_id(..., for_update=True)`, acquiring the per-meeting row lock (`SELECT … FOR UPDATE` with `populate_existing`) before any capacity/waiting-list read. Read-only handlers must leave it `False`. The locked load resets the acting user's `meetups`/`joined_links` to unloaded, so a handler that both locks and opted into the collections must re-load them itself. See the `database` skill's "Per-meeting row locks" section for the full convention.

Use `meeting_interaction_allowed` in every handler that acts on a meeting reachable from outside the
bot chat (join, leave, attach-to-chat, invite). The meeting id arrives in client-supplied callback
data, so it proves nothing on its own: the guard authorizes the *message the tap came from*, which
Telegram fills in. It allows public meetings, owners, members (waiting list included), inviters, a
tracked message of this meeting, a meeting already tracked in this chat, and a shared card claimed by
this meeting. A shared card is claimed when it is sent, by the `chosen_inline_result` handler — an
unclaimed inline message authorizes nothing, since anyone can produce one by sharing a card of their
own. Call it before any write or render, and return immediately when it is `False` (it has already
answered the caller with the deleted-meeting alert).

`custom_keyboard` replaces the default main-menu back button as the back-navigation row(s) in the "meeting deleted" message and the reactivation prompt — pass it when the user should return to the list they came from. It travels to the renderer on the exception, so the guard never builds a screen itself.

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
  <rule>If a handler uses any of `current_user`, `meeting`, `valid_callback_data`, or `valid_meeting_callback_data`, it MUST be registered in `tests/bot/handlers/test_failure_modes.py` under the `CONTEXTS` list using the `Context` dataclass.</rule>
</critical_rules>
