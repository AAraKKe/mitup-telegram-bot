# Testing

## Running tests

All test commands run through Hatch in the `dev` environment:

```bash
hatch run dev:test {extra arguments}       # Run tests with parallel workers
hatch run dev:test-cov {extra arguments}   # Run tests with coverage (outputs report.json, coverage.xml)
```

## Test structure

Test modules mirror the `mitup_bot/` package structure. Example:

```
mitup_bot/handlers/main_menu/create_meeting.py
  →  tests/handlers/main_menu/test_create_meeting.py
```

## Parameterization

When multiple tests exercise the same logic with different inputs, combine them using `@pytest.mark.parametrize` to avoid duplication.

## Mocking

No real external services are used in tests. Both the database and the Telegram Bot API are mocked.

### Database

Use the `mock_session` fixture (globally available). It provides a mock `Session` object that can be configured per test.

### Telegram API

Use the `api` fixture, backed by the `MockApi` class (not a plain `mock.Mock`). The `api` fixture patches the API import for a specific module, so it is defined in each test file (or module `conftest.py`) where it is needed:

```python
@pytest.fixture()
def api(mocker):
    mock_api = MockApi()
    mocker.patch("mitup_bot.handlers.meeting.show_meeting.api", mock_api)
    return mock_api
```

If only a few tests in a file need API mocking, use `mocker.patch` inline instead of a fixture.

## Calling handlers

Never call handler functions directly — this skips the registry's argument injection and metrics tracking. Use the `call_handler` helper from `tests.helpers.context`:

```python
from tests.helpers.context import call_handler

result = await call_handler(MyHandlerId.SHOW, update, context)
```

This also supports simulating multi-step conversations. See `tests/handlers/meeting/edit_meeting/test_edit_meeting_datetime.py` for examples.

## Updates

Use the `UpdateRequest` dataclass (from `tests.helpers.fixtures`) as an indirect parameter to the globally available `update` fixture. This avoids manually constructing `telegram.Update` objects in every test:

```python
@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.SHOW_MEETING.with_id(1))], indirect=True)
async def test_show_meeting(update, context, mock_session):
    ...
```

## Creating models

Use the `create_*` helpers from `tests.helpers` to construct model instances. **Never instantiate models directly** — always use the corresponding factory function so tests specify only the fields relevant to the scenario:

| Helper | Model |
|--------|-------|
| `create_user()` | `User` |
| `create_meetup()` | `Meetup` |
| `create_settings()` | `Settings` |
| `create_joined_link()` | `JoinedUsers` |
| `create_message()` | `Message` (the DB model, not `telegram.Message`) |

```python
from tests.helpers import create_user, create_meetup, create_message

user = create_user(id=1, first_name="John", tg_user_id=123)
message = create_message(inline_message_id="msg_1", chat_instance="someinstance")
```

For handler tests that need a fully configured user, use the `user_with_settings` fixture — it creates a `User` with an associated `Settings` object ready for handler invocations.

> **Name collision:** `tests/helpers/fixtures.py` imports `Message` from `telegram`. The DB model is imported as `MeetupMessage` there. When adding new helpers that work with the DB `Message`, use the alias.

## Inline message tests

`UpdateRequest(from_bot_chat=False)` creates a callback query with `inline_message_id` and no `effective_chat`/`effective_message`. This is how shared (inline) messages work in Telegram.

Key differences from bot-chat tests:

- `chat_instance` defaults to `"someinstance"` in the test fixtures.
- `inline_message_id` defaults to `"some_inline_message_id"`.
- `MockApi` assertion helpers (e.g. `assert_edit_message_called`) support both bot-chat and inline updates.

## Metric assertions

When asserting metrics that include `MetricKey.TIME`, always pass the `units` parameter explicitly. `TIME` uses `Unit.MILLISECONDS`, while the other standard metrics use `Unit.COUNT`:

```python
context.metrics_engine.assert_metrics_emited(
    [MetricKey.STALE_MEETING_MESSAGE, MetricKey.FAULT, MetricKey.TIME, MetricKey.DB_CONNECTIONS_LEAKED],
    [1.0, 0.0, AnyFloat(), 0],
    [Unit.COUNT, Unit.COUNT, Unit.MILLISECONDS, Unit.COUNT],
    add_handler_dimensions=False,
)
```

Omitting `units` causes a silent mismatch because the default unit is `Count` and `Time` is emitted as `Milliseconds`.

## Failure mode tests

Common error scenarios (user not found, meeting not owned, malformed callback data, etc.) are tested centrally in `tests/test_failure_modes.py`. This avoids repeating the same assertions across every handler test file.

### When to register a handler

**Every new handler** that uses any of these guards must be added to the `CONTEXTS` list in `test_failure_modes.py`:

- `guards.current_user()` → `ErrorMode.USER_NOT_FOUND`
- `guards.meeting_accessible()` → `ErrorMode.MEETING_NOT_OWNED`, `ErrorMode.MEETING_NOT_FOUND`
- `guards.valid_callback_data()` / `guards.valid_meeting_callback_data()` → `ErrorMode.MALFORMED_CALLBACK_DATA`
- Context data access (e.g., `context.get_meeting_id()`) → `ErrorMode.MISSING_USER_DATA`

### Adding a handler

```python
Context(
    handler_id=EditMeetingHandlerId.YOUR_HANDLER,
    update_request=UpdateRequest(callback_query=cb.YOUR_CALLBACK.with_id(MEETING_ID_NOT_OWNED)),
    error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
    id="your_handler_name",
),
```

### Optional `Context` parameters

| Parameter | Purpose |
|-----------|---------|
| `custom_keyboard` | Custom inline keyboard expected in the error view |
| `meeting_id` | `dict[ContextId, int]` to pre-populate context data for conversation handlers |
| `metrics_emitted` | `MetricsProperties` for additional metrics expected during the test |
| `metrics_properties` | `dict` of dimension key-values to attach to emitted metrics |

### Example with context data

```python
Context(
    handler_id=EditMeetingHandlerId.SET_TIME_MESSAGE,
    update_request=UpdateRequest(message_text="12:00"),
    error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
    id="set_meeting_time_message",
    meeting_id={ContextId.EDIT_MEETING_TIME: 99},
    metrics_emitted=MetricsProperties(
        metrics=["CleanUserData"],
        values=[1],
        units=[Unit.COUNT],
    ),
    metrics_properties={"ContextId": ContextId.EDIT_MEETING_TIME.value},
),
```
