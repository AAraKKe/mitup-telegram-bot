# Python Tests

## Parameterizing tests

When there are multiple tests that do basically the same thing, try combining them with a pytest parameterization to avoid code duplication.

## Running tests

Tests are always run through a hatch command. All commands we run while developing are part of the `dev` environmen in hatch. To run tests:

```bash
hatch run dev:test {extra arguments}
```

### Command to run to check coverage

If we want to check the coverage of a given test we run it with hatch command:

```bash
hatch run dev:test-cov {extra arguments}
```

This will output the coverage into the file `report.json` with the output of the coverage python package.

## Tests structure

Tests should be defined in modules that mimic the structure of the ones under mitup_bot.

For example, if I want to test code wriyten in the mitup_bot.handlers.main_menu.create_meeting.py, the tests should be located under tests.handlers.main_menu.test_create_meeting.py

## Mocking

We never use real external service outside the test environment. This means that the database interaction is mocked as well as the telegram bot api. Tools are provided for this.

The database interaction is mocked through the `mock_session` fixture available to all tests and the api interaction is mocked with the `api` fixture which is available through every module conftest.

The `api` fixture is a bit special because we are mocking the import of the `api` package on a specefic module. For this reason we define it in every test file were we need it. For example, to test
the `mitup_bot.handlers.meeting.edit_meeting` file, the `api` fixture is defined in the `tests.handlers.meeting.test_edit_meeting.py` file. This, of course, depends on the use case. If a given test module
does not require the api fixture for most of the tests, we could use the mock api just on the test we need it too instead of using it as a fixture. It is important to note that the api is mocked through the `MockApi` class and not through
a simple mock.

## Calling handlers

Avoid calling handler methods directly, this skips some verifications done at the registry level that could be important when testing that a request is properly handled. Instead, use the call_handler method under tests.helpers.context.

This allows to also mimic conversations with a given user, as an example check the tests in tests/handlers/meeting/edit_meeting/test_edit_meeting_datetime.py

## Providing updates to the tests

When we need to use the Telegram.Update object on a given test, we can provide this through a parameter of type UpdateRequest (under tests.helpers.fixtures). This UpdateRequest object is used as an indirect parameter to the `update` fixture
that is globally available. We do not need to mock every update in every test. See, again, `tests/handlers/meeting/edit_meeting/test_edit_meeting_datetime.py` foran example about how UpdateRequest is used.

## Creating models
When you need to create a model (e.g. User, Meetup, etc.) you can use the create_* helper methods that are available in tests.helpers module. For example, to create a user you can do:

```python
from tests.helpers import create_user

user = create_user(id=1, first_name="John", tg_user_id=123)
```

This will create a user with the given id, first name and telegram user id.

There are specific fixtures to create models that work right away when testing handlers. The fixture `user_with_settings` will create a user with a settings object associated to it. Use this fixture in a test where
we need to call a handler because the user has all the properties needed to operate as a real user.

## Failure Mode Tests

To avoid repeating the same failure tests for every handler, we have a centralized failure mode testing system in `tests/test_failure_modes.py`. This file contains parameterized tests that automatically test common error scenarios for all registered handlers.

### When to Add Handlers to Failure Mode Tests

**IMPORTANT**: Whenever you create a new handler that uses any of the following:
- `guards.current_user()` - to get the current user
- `guards.meeting_accessible()` - to check meeting ownership
- `guards.valid_callback_data()` or `guards.valid_meeting_callback_data()` - to parse callback data
- Context data (e.g., `context.get_meeting_id()`) - to retrieve stored meeting IDs

You **must** add it to the `CONTEXTS` list in `tests/test_failure_modes.py`.

### Available Error Modes

The following error modes are available and should be used based on what your handler validates:

- `ErrorMode.MEETING_NOT_OWNED` - User tries to access a meeting they don't own
- `ErrorMode.MEETING_NOT_FOUND` - Meeting doesn't exist in the database
- `ErrorMode.USER_NOT_FOUND` - User is not registered in the database
- `ErrorMode.MALFORMED_CALLBACK_DATA` - Callback data is missing required parameters
- `ErrorMode.MISSING_USER_DATA` - Required context data is not set

### How to Add a Handler

Add a `Context` object to the `CONTEXTS` list with the appropriate error modes:

```python
Context(
    handler_id=EditMeetingHandlerId.YOUR_HANDLER_CALLBACK,
    update_request=UpdateRequest(callback_query=cb.YOUR_CALLBACK.with_id(MEETING_ID_NOT_OWNED)),
    error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
    id="your_handler_name",
),
```

### Example: Adding Language Handlers

Here's an example of how the language handlers were added:

```python
# Handler that checks meeting ownership
Context(
    handler_id=EditMeetingHandlerId.LANGUAGE_CALLBACK,
    update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_LANGUAGE.with_id(MEETING_ID_NOT_OWNED)),
    error_modes={ErrorMode.MEETING_NOT_OWNED, ErrorMode.USER_NOT_FOUND},
    id="edit_meeting_language",
),

# Handler that validates callback data
Context(
    handler_id=EditMeetingHandlerId.LANGUAGE_CALLBACK,
    update_request=UpdateRequest(callback_query=cb.EDIT_MEETING_LANGUAGE),
    error_modes={ErrorMode.MALFORMED_CALLBACK_DATA},
    id="edit_meeting_language_malformed",
),
```

### Special Cases

Some handlers may need additional configuration:

- **Custom keyboards**: Use `custom_keyboard` parameter if the error view has a custom keyboard
- **Context data**: Use `meeting_id` parameter to pre-populate context data (e.g., for conversation handlers)
- **Additional metrics**: Use `metrics_emitted` to specify extra metrics that should be emitted during the test
- **Metrics properties**: Use `metrics_properties` to add properties to the emitted metrics

Example with context data:

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
        units=[Unit.COUNT]
    ),
    metrics_properties={"ContextId": ContextId.EDIT_MEETING_TIME.value},
),
```

### Benefits

By adding your handlers to the centralized failure mode tests, you ensure:
1. Consistent error handling across all handlers
2. Proper metrics emission for all failure scenarios
3. Reduced test code duplication
4. Easy maintenance when error handling patterns change
