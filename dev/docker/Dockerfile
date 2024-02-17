FROM python:3.11-slim-bookworm

# Set the working directory to /mitup-telegram-bot
WORKDIR /mitup-telegram-bot

# Install libraries needed to run pyscopg2
RUN apt-get update && \
    apt-get install -y libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

# Install Hatch
RUN pip install hatch

# Copy bot into container
COPY mitup_bot /mitup-telegram-bot/mitup_bot
COPY pyproject.toml /mitup-telegram-bot/pyproject.toml
COPY LICENSE /mitup-telegram-bot/LICENSE

# Setup hatch prod environment
RUN hatch run prod:help

# Launch the bot
CMD hatch run prod:launch