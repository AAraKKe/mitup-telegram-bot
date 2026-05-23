---
icon: material/wrench-outline
---

# Setup

This guide walks you through setting up your local development environment.

## Contributor requirements

Mitup is built in Python, deployed in containers on AWS and managed using [Hatch](https://hatch.pypa.io) environments. Contributors must have:

*   A Docker installation (install it from [here](https://www.docker.com/products/docker-desktop/) if you don't have it)
*   Knowledge of modern Python, including type annotations
*   A GitLab account

## Tools

### Hatch

If you do not have a working installation of Hatch, install it now. We recommend installing Hatch through [pipx](https://pipx.pypa.io/stable/installation/).

```bash
pipx install hatch
```

Since we run all commands as part of the Hatch `dev` environment, there's no need to install a specific Python distribution. Hatch manages this automatically when running any command. Mitup is configured to use a specific version of Python, and Hatch attempts to locate a compatible version on your system. If none is found, Hatch installs the required version for the virtual environment `dev`.

### Pre-commit

We use [pre-commit](https://pre-commit.com/) to handle validations for each commit to the repository. Make sure to install it before committing any code to be pushed to the GitLab repo.

```bash
pipx install pre-commit
```

### Gettext and libpq

While most of the dependencies are handled by Hatch, there are two libraries that need to be installed on your system before you can run Mitup:

*   [gettext](https://www.gnu.org/software/gettext/) is used to handle translation files. Follow the instructions on their site to install it.
*   [psycopg2](https://www.psycopg.org/docs/install.html#install-from-source) is a Python library that handles PostgreSQL communication and is built as a wrapper around [libpq](https://www.postgresql.org/docs/current/libpq.html), the PostgreSQL client library. It requires building parts of the library from source, which necessitates a C compiler and several additional libraries.

??? info "Installing Required Libraries"
    === "MacOS"
        Simply install `postgresql` from Homebrew. It comes bundled with `libpq`. Installing PostgreSQL doesn't require running a server, but having it available can be useful during the development process in case you need it.

        ```bash
        brew install postgresql
        ```

        If you do not want to install PostgreSQL, you can just install the `libpq` client which comes with the necessary headers included.

        ```bash
        brew install libpq
        ```

    === "Linux"
        The `libpq` client library is supported in many Linux distributions. For a Debian-based system, you can run the following commands. You can find instructions online to install this library for any distribution.

        ```bash
        apt update
        apt install libpq-dev
        ```

    === "Windows"
        We currently do not develop on Windows and lack instructions about how to install the required libraries. We cannot validate any found information as we lack a Windows system for testing. We welcome contributions with instructions for Windows users.

### Set up local repository

Start by cloning the Mitup code from our [public repo](https://gitlab.com/meetupbot/mitup-telegram-bot)

```bash
git clone git@gitlab.com:meetupbot/mitup-telegram-bot.git
cd mitup-telegram-bot
```

Set up `pre-commit`:

```bash
pre-commit install
pre-commit run --all-files
```

Now let's run the validations. This command will trigger the creation of the `dev` Hatch environment, which is used for all development-related activities.

```bash
hatch run dev:validate
```

!!! info "If you use VSCode"
    If you use VSCode, a setup script is included in the repo. This script provides the necessary VSCode configuration needed to run type checking and formatting correctly. Run:
    ```
    hatch run dev:setup-vscode
    ```

    !!! tip
        For better IDE experience with type hints, you can use the `ide` environment (`hatch shell ide`) which includes boto3-stubs for enhanced autocompletion and type checking.

### Configure your development bot

To test your changes through Telegram, you need to link a bot to Mitup. Head to [BotFather](https://t.me/BotFather) and register a new bot:

*   Run the `/newbot` command
*   Choose a name for the bot, e.g. `Mitup-<yourname>-dev`
*   Choose a username for the bot, e.g. `mitup_yourname_dev_bot`
*   Copy the token provided by BotFather

Next, run the following command:

```bash
hatch run dev:set-local-bot <token>
```

This creates a `dev.toml` configuration file used by Mitup when running the bot locally via Docker.

### Set up your database

For local execution, we rely on a Docker instance of PostgreSQL. First, run all database migrations to ensure the database schema is correctly set up:

```bash
docker compose run migrations-upgrade
```

This command spins up a PostgreSQL database with a local volume in `./postgres_data` that persists data between executions, and then runs the necessary migrations.

## Launch Mitup

Once setup is complete, start the bot:

```bash
docker compose run mitup
```

Open the bot in Telegram via BotFather.
