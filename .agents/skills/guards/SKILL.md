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
| `current_user` | `(update, session)` | `User` | `UserNotFound` (caught by global error handler) |
| `member_user` | `(update, session)` | `User \| None` (only when status is MEMBER) | — |
| `user_language` | `(update, session)` | `str` (lang code or fallback) | — |
| `user_registered` | `(update, session, context, alert_message)` | `User \| None` | — (answers callback query with alert) |

Use `current_user` for all handlers that require an authenticated user. Use `user_registered` only when an unauthenticated user is a valid, non-fatal case (e.g. inline query handlers). `member_user` gates `/start` routing between the member flow and re-onboarding.

All guards that take a `session` are async — `await` them.

### Message and query access

| Function | Signature | Returns | Raises |
|----------|-----------|---------|--------|
| `message` | `(update)` | `Message` | `EffectiveMessageNotSet` |
| `chat` | `(update)` | `Chat` | `EffectiveChatNotSet` |
| `callback_query` | `(update)` | `CallbackQuery` | `CallbackQueryNotSet` |
| `valid_inline_query` | `(update)` | `InlineQuery` | `InlineQueryNotSetError` |

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
| `meeting_accessible` | `(session, user, meeting_id, action, update, context, custom_keyboard=None, for_update=False)` | `Meetup \| None` | — (handles redirect internally) |
| `meeting_viewable` | `(session, user, meeting_id, action, update, context, custom_keyboard=None)` | `Meetup \| None` | — (handles redirect internally) |
| `user_owns_meeting` | `(user, meeting_id, action, update, context, redirect=True)` | `Meetup \| None` | — (handles redirect internally) |

Use `meeting_accessible` for all handlers that require **ownership** of a meeting from the bot chat (not inline). It handles three cases internally: meeting not found → "meeting deleted" message; meeting inactive + owner → reactivation prompt; non-owner → redirect to main menu. When `None` is returned, the handler must return immediately.

Pass `for_update=True` when the handler goes on to mutate participants or capacity: the guard then loads the meeting via `Meetup.by_id(..., for_update=True)`, acquiring the per-meeting row lock (`SELECT … FOR UPDATE` with `populate_existing`) before any capacity/waiting-list read. Read-only handlers must leave it `False`. See the `database` skill's "Per-meeting row locks" section for the full convention.

Use `meeting_viewable` for handlers that only need to **display** a meeting the user can reach without owning it — e.g. the "Joined meetings" list. It behaves like `meeting_accessible` for the not-found and inactive-owner cases, but a non-owner who has *joined* an active meeting is allowed through so the caller can render `Meetup.view_for(user)` (owner → `main_view`, non-owner → `external_view`). Non-owners are still redirected to the main menu for meetings they neither own nor joined, and for inactive meetings (only the owner can reactivate).

`user_owns_meeting` is a lower-level guard that skips the not-found and inactive checks. Use it only when those cases are handled separately.

For both `meeting_accessible` and `meeting_viewable`, `custom_keyboard` replaces the default main-menu back button as the back-navigation row(s) in the "meeting deleted" message and the reactivation prompt — pass it when the user should return to the list they came from.

## Usage pattern

```python
@HandlersRegistry.register_callback_query(MyHandlerId.SHOW, callback_data=cb.MY_CALLBACK)
@with_session
async def show(session: AsyncSession, update: Update, context: TMitupContext):
    meeting_id = guards.valid_callback_data(cb.MY_CALLBACK.parse(context.match), MyHandlerId.SHOW).id
    user = await guards.current_user(update, session)
    meeting = await guards.meeting_accessible(session, user, meeting_id, "show meeting", update, context)
    if meeting is None:
        return
    # ... proceed with meeting
```

## Failure mode registration

<critical_rules>
  <rule>If a handler uses any of `current_user`, `meeting_accessible`, `meeting_viewable`, `valid_callback_data`, or `valid_meeting_callback_data`, it MUST be registered in `tests/bot/handlers/test_failure_modes.py` under the `CONTEXTS` list using the `Context` dataclass.</rule>
</critical_rules>
