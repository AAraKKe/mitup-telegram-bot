# Agents

This file contains the rules that were previously in the `.cursor` folder.

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
- handler_id contains the definition of a handler id, used to identify each handler.
- custom_context contains the custom PTB context for the bot. This defines methods to access telemetry and emit it among other things
- db contains the necessary tooling to interact with the database
- guards are a set of methods that are used to validate input received by a handler
- timezone_api contains the logic to interact with the google timezone api
- translations defines the translations engine, a wrapper around gettext to translate text
