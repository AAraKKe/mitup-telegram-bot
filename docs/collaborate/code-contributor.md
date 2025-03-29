# Become a Code Contributor :computer:

If you are here it means you are interested in helping build Mitup, thanks a lot! We aim to help Mitup grow to suit the maximum number of users possible. However, we have limited time and community contributions are greatly appreciated to help with bug fixes and new features.

Follow the guides below to become a contributor.

## Contributor requirements

Mitup is built in Python, deployed in containers in AWS and managed through [Hatch](https://hatch.pypa.io) environments. Contributors must have:

- A Docker installation (install it from [here](https://www.docker.com/products/docker-desktop/) if you don't have it)
- Knowledge of modern Python, including type annotations
- A GitLab account

## Setup

### Hatch

If you do not have a working installation of Hatch, install it now. We recommend installing Hatch through [pipx](https://pipx.pypa.io/stable/installation/).

```bash
pipx install hatch
```

Since we run all commands as part of the Hatch `dev` environment, there is no need to install a specific Python distribution. Hatch manages this automatically when running any command. Mitup is configured to use a specific version of Python, and Hatch will attempt to locate a compatible version on your system. If none is found, Hatch will install the required version for the virtual environment `dev`.

### Setup the repo

Start by getting the Mitup code from our [public repo](https://gitlab.com/meetupbot/mitup-telegram-bot)

```bash
git clone git@gitlab.com:meetupbot/mitup-telegram-bot.git
cd mitup-telegram-bot
```

Now let's run the validations. This command will trigger the creation of the `dev` Hatch environment, which is used for all development-related activities.

```bash
hatch run dev:validate
```

!!! info "If you are using VSCode"
    If you are using VSCode, a setup script is included in the repo. This provides with the VSCode configuration needed to be able to run type checking and formatting accordingly. Run:
    ```
    hatch run dev:setup-vscode
    ```

### Configure your development bot

To test your changes through Telegram, you'll need to link a bot to Mitup. Head to [BotFather](https://t.me/BotFather) and register a new bot:

- Run the `/newbot` command
- Choose a name for the bot, e.g. `Mitup-<yourname>-dev`
- Choose a username for the bot, e.g. `mitup_yourname_dev_bot`
- Copy the token provided by BotFather

Next, run the following command:

```bash
hatch run dev:set-local-bot <token>
```

This creates a `dev.toml` configuration file that Mitup uses when running the bot locally in Docker.

### Setup your database

For local execution, we rely on a Docker instance of PostgreSQL. First, run all database migrations to ensure proper schema setup:

```bash
docker compose run migrations-upgrade
```

This command spins up a PostgreSQL database with a local volume in `./postgres_data` that persists data between executions and runs the necessary migrations.

After completion, you can start the bot by running:

```bash
docker compose run mitup
```

and begin using it from Telegram by opening the bot you created through BotFather.
