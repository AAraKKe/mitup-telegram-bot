# Agents

This file contains the rules that were previously in the `.cursor` folder.

## Building Handlers

A handler is a python method that needs to be decorated with a @HandlerRegistry command. This method is the callback of the handler and will be called with the update to be handled by the handler. There are different commands depending on the hanlder type to register:

- register_command: used to register command handlers
- register_message: used to register a handler that will pick up any message sent by the user
- register_callback_query: this type of handler gets user request when they click on a button in the telegram bot. Callback queries usually have callback_data attached to it.
- register_conversation_handler: this is a special type of handlers for handling long conversations with different states depending on what the user answers. These receive a list of other handlers that have different roles:
  - entry_point_handlers_names: list of callback ids from the handlers that should trigger the beginning of the conversation.
  - states: a dictionary where the keys are enums (returned by any of the handlers in the conversations) and the values are a list of callback ids. This allows the conversation to flow through different states.
  - fallbacks: this defines the states the conversation could go through if none of the previous states matches.
- register_inline_handler: this is a handler for inline queries.

Handlers can also have filters. Filters are used to identify whether a given update should be handled by that handler or not.

### Adding a database session to a handler

If a handler needs a database session, which most of them do, the handler callback should also be decorated with a with_assync_session decorator. This method is part of the mitup_bot.db module. This injects a new argument to the handler, session, that has a database session withan open trasaction associated to it.

## Documentation

### Style when writing markdown

- Be friendly without sounding too over the top
- When explaining something technical, be sure to add code blocks that can be expanded with a heading explaining what is hidden. Only make it expandable if the content is more than 10 lines of code.
- Try to use emojis for conveying emotions. Mostly on headers and never within paragraphs. Use the twimoji emojis instead of simple emojis, e.g. :pen: or :check_mark:
- When adding examples, do not assume meeting only refers to business meetings. These refer to all types of meetings, mostly with friends. Add examples that showcase different situations.
- When adding bullet points use `*` instead of `-` because some times they do not render properly.
- Any new file should be named in snake_case.

### Documentation Files

Documentation files, under the `docs` folder, are served with mkdocs. The mkdocs config file in the root of the project is used to handle mkdocs configuration.

Every time a new page is added to the documentation we need to ensure that it is added to the appropriate place in the mkdocs file to be accesible through the navigation in the docs site.

### Linking non-doc files

Any non documentation file should be linked with the full url of the Mitup repository pointing to the main branch. This is because the project files are not deployed with the documentation.

### Heading

When creating a heading (either through standard markdown heading or in bold to simulate a heading) always have the heading surrounded by blank lines to ensure that formatting is correct.

For example:

```markdown
## This heading
- Won't
- Properly generate
- The bullet point list

## But this heading

- Would do it
- Properly
```

The same applies for any bullet point, always surround them with blank lines

### End of line

End every file with an empty line.

### Buttons

When referring to buttons from the bot interface within the documentation:

1. Identify the specific button mentioned (e.g., "New meeting", "Settings").
2. Find the corresponding entry in the `ButtonMessages` class (`mitup_bot/utils/messages.py`) to confirm the exact button text and its associated Unicode emoji (e.g., `➕ New meeting`).
3. Determine the correct Twemoji shortcode for the Unicode emoji (e.g., `➕` is `:heavy_plus_sign:`). You can usually find these shortcodes with a quick web search or by referring to a Twemoji cheat sheet.
4. Format the button reference in the Markdown file using the following pattern: `*:twemoji_shortcode: Button Text*{.button-like}`.
    - **Example:** `*:heavy_plus_sign: New meeting*{.button-like}`
5. **Important:**
    - Wrap the formatted text (including the class attribute) in Markdown italics (`*...*`).
    - Do **not** use monospace/code backticks (`` `...` ``).
    - The `.button-like` class is essential and must be included exactly as shown, *inside* the closing asterisk.
6. The visual styling (italic text, light grey background, rounded corners) is handled automatically by the custom CSS associated with the `.button-like` class (`docs/assets/stylesheets/main.css`) and should not be added manually in the Markdown."

## Python Tests

### Parameterizing tests

When there are multiple tests that do basically the same thing, try combining them with a pytest parameterization to avoid code duplication.

### Running tests

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

### Tests structure

Tests should be defined in modules that mimic the structure of the ones under mitup_bot.

For example, if I want to test code writen in the mitup_bot.handlers.main_menu.create_meeting.py, the tests should be located under tests.handlers.main_menu.test_create_meeting.py

### Mocking

We never use real external service outside the test environment. This means that the database interaction is mocked as well as the telegram bot api. Tools are provided for this.

The database interaction is mocked through the `mock_session` fixture available to all tests and the api interaction is mocked with the `api` fixture which is available through every module conftest.

The `api` fixture is a bit special because we are mocking the import of the `api` package on a specefic module. For this reason we define it in every test file were we need it. For example, to test
the `mitup_bot.handlers.meeting.edit_meeting` file, the `api` fixture is defined in the `tests.handlers.meeting.test_edit_meeting.py` file. This, of course, depends on the use case. If a given test module
does not require the api fixture for most of the tests, we could use the mock api just on the test we need it too instead of using it as a fixture. It is important to note that the api is mocked through the `MockApi` class and not through
a simple mock.

### Calling handlers

Avoid calling handler methods directly, this skips some verifications done at the registry level that could be important when testing that a request is properly handled. Instead, use the call_handler method under tests.helpers.context.

This allows to also mimic conversations with a given user, as an example check the tests in tests/handlers/meeting/edit_meeting/test_edit_meeting_datetime.py

### Providing updates to the tests

When we need to use the Telegram.Update object on a given test, we can provide this through a parameter of type UpdateRequest (under tests.helpers.fixtures). This UpdateRequest object is used as an indirect parameter to the `update` fixture
that is globally available. We do not need to mock every update in every test. See, again, `tests/handlers/meeting/edit_meeting/test_edit_meeting_datetime.py` foran example about how UpdateRequest is used.

## Repo Info

### Information about the Mitup repo

If at any point a link to the mitup repo needs to be added somewhere, the repo is located here: <https://gitlab.com/meetupbot/mitup-telegram-bot>. Any link needs to follow gitlab url rules, not githubs.

If a quick link to a new issue wants to be added, use the issue templates under `.gitlab/issue_templates` to know which ones can be used and add it the link.

### Folder structure

The bot is a python Telegram bot and the main codebase is placed on the mitup_bot folder that contains several submodules.

- cli: Contains the cli tooling used to operate the bot and its CI
- environments: hold the different configuration files for each environment we want to run the bot in
- handlers: this is where most of the logic of the bot is. We use [Telegram Python Bot](mdc:https:/docs.python-telegram-bot.org/en/stable/index.html) (PTB) as the sdk to develop the bot and all the bot behavior is defined through handlers.
  - Handlers are organized in submodules semantically defined. We have submodules roughly referencing each part of the bot features or areas.
  - Each sub module in the handlers module contains 2 main modules: `enums` and `entry`. These reference all enums used to identify handlers or conversations and the callback that is the entry point for that feature.
  - These do not have any runtime implication and is just a way of being able to quickly identify where a piece of code can be.
- The lambdas module contains the code that is run as a lambda function in AWS
- locales: contains all translations of the bot
- migrations: this is a folder used to run [alembic](mdc:https:/alembic.sqlalchemy.org/en/latest) which is the database migrations tool we use
- models: this contains all the database models used in the bot
- monitoring: includes the necessary tooling to emit metrics to CloudWatch
-- utils: this contains several utilities used around the bot. The most important ones are `messages` and `callbacks`. Messages contain the english version of any text that appears in the bot and `callbacks` contains general callbacks that represent the callback data of a request to PTB
- views: contains all the views defined in the bot. In order to abstract the api calls from what we want to show in the bot, we define different views

The rest of the modules in the root of the mitup_bot folder reference direct utilities:

- api: methods to interact with the bot api
- app: defines the PTB app that is run when the bot is launched
- callback_data contains the centralized definition of how callback data is handled in a request. All callbacks in the utils.callbacks module are instances of this callback data
- callback_id contains the definition of a callback id, used to identify each handler.
- custom_context contains the custom PTB context for the bot. This defines methods to access telemetry and emit it among other things
- db contains the necessary tooling to interact with the database
- guards are a set of methods that are used to validate input received by a handler
- timezone_api contains the logic to interact with the google timezone api
- translations defines the translations engine, a wrapper around gettext to translate text

## Validate Docs

When modifying files in the doc folders, always run `hatch run dev:build-docs` to validate that the docs are building.
