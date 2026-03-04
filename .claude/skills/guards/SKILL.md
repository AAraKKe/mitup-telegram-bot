---
name: guards
description: Guards conventions for mitup_bot. Auto-load when writing handler validation, accessing the current user, validating callback data, or checking meeting access.
user-invocable: false
---

# Guards

Guards live in `mitup_bot/guards.py`. They validate handler inputs and raise domain exceptions on failure.

<critical_rules>
  <rule>Always use guards instead of manual validation inside handlers.</rule>
</critical_rules>

## Available guards

### User access

| Function | Signature | Returns | Raises |
|----------|-----------|---------|--------|
| `current_user` | `(update, session)` | `User` | `UserNotFound` (caught by global error handler) |
| `user_language` | `(update, session)` | `str` (lang code or fallback) | — |
| `user_registered` | `(update, session, context, alert_message)` | `User \| None` | — (answers callback query with alert) |

Use `current_user` for all handlers that require an authenticated user. Use `user_registered` only when an unauthenticated user is a valid, non-fatal case (e.g. inline query handlers).

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
| `meeting_accessible` | `(session, user, meeting_id, action, update, context, custom_keyboard=None)` | `Meetup \| None` | — (handles redirect internally) |
| `user_owns_meeting` | `(user, meeting_id, action, update, context, redirect=True)` | `Meetup \| None` | — (handles redirect internally) |

Use `meeting_accessible` for all handlers that operate on a meeting from the bot chat (not inline). It handles three cases internally: meeting not found → "meeting deleted" message; meeting inactive + owner → reactivation prompt; non-owner → redirect to main menu. When `None` is returned, the handler must return immediately.

`user_owns_meeting` is a lower-level guard that skips the not-found and inactive checks. Use it only when those cases are handled separately.

## Usage pattern

```python
@HandlersRegistry.register_callback_query(MyHandlerId.SHOW, callback_data=cb.MY_CALLBACK)
@with_async_session
async def show(session: Session, update: Update, context: TMitupContext) -> None:
    meeting_id = guards.valid_callback_data(cb.MY_CALLBACK.parse(context.match), MyHandlerId.SHOW).id
    user = guards.current_user(update, session)
    meeting = await guards.meeting_accessible(session, user, meeting_id, "show meeting", update, context)
    if meeting is None:
        return
    # ... proceed with meeting
```

## Failure mode registration

<critical_rules>
  <rule>If a handler uses any of `current_user`, `meeting_accessible`, `valid_callback_data`, or `valid_meeting_callback_data`, it MUST be registered in `tests/test_failure_modes.py` under the `CONTEXTS` list using the `Context` dataclass.</rule>
</critical_rules>
