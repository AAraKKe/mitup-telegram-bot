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
