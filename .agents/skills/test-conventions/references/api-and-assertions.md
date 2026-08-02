# MockApi and Assertion Helpers

## MockApi

`MockApi` (from `tests.helpers.api`) is a drop-in replacement for `TelegramApi`. It overrides all API methods with mocks and provides typed assertion helpers. It's automatically created by `call_handler` and accessible via `context.api`.

**Critical**: overridden methods in `MockApi` are regular functions, NOT `async def`. This avoids double-wrapping coroutines. If you need to add a new method override, follow this pattern.

## Typed assertion helpers

Always use these instead of raw `assert_method_just_called` + `call_args`. The typed helpers provide diff-based error messages via `deepdiff` that show exactly what differed.

### `context.api.assert_send_message_called(update, view, times=1)`

Asserts `send_message` was called with the given update and view.

### `context.api.assert_edit_message_called(update, view, times=1)`

Asserts `edit_message` was called with the given update and view.

### `context.api.assert_answer_callback_query_called(update, text=None, show_alert=False, times=1)`

Asserts `answer_callback_query` was called. Pass `text` and/or `show_alert` as needed.

### `context.api.assert_answer_inline_query_called(update, results, button=None, cache_time=None, times=1)`

Asserts `answer_inline_query` was called with the given results list.

### `context.api.assert_update_meeting_messages_called(meeting, current_message=DEFAULT, skip_current=None, was_deleted=None, times=1)`

Asserts `update_meeting_messages` was called. Only pass optional kwargs when you need to assert specific values.

### `context.api.assert_send_message_to_user_called(user, view, times=1)`

Asserts `send_message_to_user` was called with the given user and view.

### Negative assertions

```python
context.api.assert_method_just_called("send_message", times=0)
context.api.assert_send_message_not_called()
context.api.assert_edit_message_not_called()
context.api.assert_update_meeting_messages_not_called()
```

### Raw access (avoid unless necessary)

```python
context.api.call_args("method_name")  # Last call args
context.api.call_args_list("method_name")  # All call args
context.api.assert_method_just_called("method_name", times=N)  # Called N times, any args
```

## Diff-based assertions

Under the hood, `MockApi` uses `assert_awaited_once_with_diff` and `assert_awaited_with_diff` from `tests.assertions`. These use `deepdiff.DeepDiff` to produce readable diffs when assertions fail, showing exactly which fields differ between expected and actual calls. You don't need to use these directly — the typed helpers on `MockApi` call them for you.

## Views in assertions

Handler tests typically assert that a specific view was sent. Construct the expected view using `factory` functions from `mitup_bot.views.factory`:

```python
from mitup_bot.views import factory

context.api.assert_edit_message_called(
    update,
    factory.confirmation_view(
        lang=user.lang,
        message=MeetingMessages.DELETE_MEETING.get(lang=user.lang),
        confirm_callback_data=cb.CONFIRM_DELETE_MEETING.with_id(1),
        decline_callback_data=cb.DECLINE_DELETE_MEETING.with_id(1),
    ),
)
```

For model-owned views (like meeting detail), use `meeting_views.main_view(meeting)` from `mitup_bot.views.meeting`.
