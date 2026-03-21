# Failure Modes Test Module

## Purpose

`tests/handlers/test_failure_modes.py` centralizes tests for common guard failures. Instead of repeating "user not found" or "meeting not owned" tests in every handler test file, you register the handler in `CONTEXTS` and the module generates parametrized tests for each error mode.

**Rule**: Do not test common guards in individual handler test files. If a handler uses guards, register it here.

## How it works

The module defines `ErrorMode` enum values for each failure type:

| ErrorMode | What it tests |
|---|---|
| `MEETING_NOT_OWNED` | User exists but doesn't own the meeting |
| `USER_NOT_FOUND` | No user found for the Telegram user ID |
| `MEETING_NOT_FOUND` | Meeting ID doesn't exist in DB |
| `MALFORMED_CALLBACK_DATA` | Callback data missing required fields (e.g., no meeting ID) |
| `MISSING_USER_DATA` | User context data not populated |
| `MEETING_INACTIVE_OWNER` | Meeting is inactive, tested for owner |

## Registering a handler

Add a `Context` entry to the `CONTEXTS` list:

```python
Context(
    handler_id=EditMeetingHandlerId.DATE_TIME_ENTRY_CALLBACK,
    update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_DATE_TIME.with_id(MEETING_ID_NOT_OWNED)),
    error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
    id="edit_meeting_date_time_entry",
)
```

### Context fields

| Field | Type | Required | Purpose |
|---|---|---|---|
| `handler_id` | `HandlerId` | Yes | The handler to test |
| `update_request` | `UpdateRequest` | Yes | The update that triggers the handler |
| `id` | `str` | Yes | Unique test ID for parametrization |
| `error_modes` | `set[ErrorMode]` | Yes | Which failure modes to test |
| `user_fixture` | `str` | No | Fixture name for the user (default: `"user_with_settings"`) |
| `fault_count` | `int` | No | Expected fault metric value (default: `0`) |
| `custom_keyboard` | `Keyboard \| None` | No | Custom keyboard for "meeting not found" response |
| `meeting_id` | `dict[ContextId, int] \| None` | No | Pre-populate context meeting IDs |
| `metrics_emitted` | `MetricsProperties` | No | Additional metrics the handler emits beyond standard ones |
| `metrics_properties` | `dict[str, str] \| None` | No | Properties for metric assertions |
| `metrics_properties_not_found` | `dict[str, str] \| None` | No | Override properties for "not found" case |
| `shows_deleted_message_when_not_found` | `bool` | No | `False` for handlers using `user_owns_meeting` directly |
| `reactivation_back_keyboard_factory` | `Callable[[str], Keyboard] \| None` | No | For inactive meeting reactivation prompt |

### Sentinel values

Constants used for meeting IDs in the CONTEXTS list:

```python
MEETING_ID_NOT_OWNED = 99    # Exists but owned by another user
MEETING_ID_NOT_FOUND = 9999  # Does not exist in DB
MEETING_ID_INACTIVE = 88     # Exists but inactive
```

### MetricsProperties

When a handler emits additional metrics beyond the standard ones (FAULT, TIME, DB_CONNECTIONS_LEAKED), specify them:

```python
Context(
    ...,
    metrics_emitted=MetricsProperties(
        metrics=["CleanUserData"],
        values=[[1, 1, 1, 1, 1, 1]],
        units=[Unit.COUNT],
    ),
)
```

## What the tests do

The module contains factory functions that filter `CONTEXTS` by error mode and generate parametrized test functions:

- `test_callback_fails_when_meeting_not_accessible` — Meeting exists but user doesn't own it
- `test_callback_fails_when_meeting_not_found` — Meeting ID not in DB
- `test_callback_fails_with_malformed_callback_data` — Callback data missing required fields
- `test_callback_fails_when_user_is_not_found` — No user for the Telegram ID
- `test_callback_fails_when_missing_necessary_user_data` — Context user data not set
- `test_owner_sees_reactivation_prompt_for_inactive_meeting` — Owner gets reactivation option
- `test_non_owner_sees_main_menu_for_inactive_meeting` — Non-owner gets redirected

Each test seeds the mock session appropriately, calls the handler, and asserts:
1. The correct view was sent (main menu, error message, etc.)
2. The correct metrics were emitted (including fault prefix, timing, and leaked connections)
