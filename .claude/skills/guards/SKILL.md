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
| `current_user` | `(update, session)` | `User` | `UserNotFound` |
| `user_language` | `(update, session)` | `str` (lang code or fallback) | — |
| `user_registered` | `(update, session, context, alert_message)` | `User \| None` | — (shows alert to user) |

`current_user` is the standard guard for all handlers that require an authenticated user. It raises `UserNotFound` which is caught by the global error handler.

### Message and query access

| Function | Signature | Returns | Raises |
|----------|-----------|---------|--------|
| `message` | `(update)` | `Message` | `EffectiveMessageNotSet` |
| `chat` | `(update)` | `Chat` | `EffectiveChatNotSet` |
| `callback_query` | `(update)` | `CallbackQuery` | `CallbackQueryNotSet` |
| `valid_callback_query` | `(update)` | `CallbackQuery` | `CallbackQueryNotSet` |
| `valid_inline_query` | `(update)` | `InlineQuery` | `InlineQueryNotSetError` |

### Callback data validation

| Function | Signature | Returns | Raises |
|----------|-----------|---------|--------|
| `valid_callback_data` | `(cb, handler_id)` | `ValidCallbackData` | `MalformedCallbackData` |
| `valid_meeting_callback_data` | `(cb, handler_id)` | `ValidMeetingCallbackData` | `MalformedCallbackData` |
| `valid_date_callback_data` | `(cb, handler_id)` | `ValidDateCallbackData` | `MalformedCallbackData` |

Always pass the `handler_id` so that `MalformedCallbackData` can be scoped to the correct handler in error reports.

### Meeting access

| Function | Signature | Returns | Raises |
|----------|-----------|---------|--------|
| `meeting_accessible` | `(session, user, meeting_id, action, update, context, custom_keyboard=None)` | `Meetup \| None` | — (handles redirect internally) |
| `user_owns_meeting` | `(user, meeting_id, action, update, context, redirect=True)` | `Meetup \| None` | — (handles redirect internally) |

`meeting_accessible` is the standard guard for any handler that operates on a meeting from the bot chat. It handles:
- Meeting not found → shows "meeting deleted" message
- Meeting inactive + user is owner → shows reactivation prompt
- User does not own meeting → redirects to main menu

## Usage pattern

```python
@HandlersRegistry.register_callback_query(handler_id=MyHandlerId.SHOW)
@with_async_session
async def show(session: Session, update: Update, context: MitupContext) -> None:
    user = guards.current_user(update, session)
    cb_data = guards.valid_callback_data(callback_data, MyHandlerId.SHOW)
    meeting = await guards.meeting_accessible(
        session, user, cb_data.id, "show meeting", update, context
    )
    if meeting is None:
        return
    # ... proceed with meeting
```

## Failure mode registration

<critical_rules>
  <rule>If a handler uses any of `current_user`, `meeting_accessible`, `valid_callback_data`, or `valid_meeting_callback_data`, it MUST be registered in `tests/test_failure_modes.py` under the `CONTEXTS` list using the `Context` dataclass.</rule>
</critical_rules>
