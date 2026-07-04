from __future__ import annotations

import contextlib
import logging
import os
import tomllib
from dataclasses import dataclass
from enum import StrEnum, auto
from importlib.resources import as_file, files
from pathlib import Path
from typing import Protocol

import structlog
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from sqlalchemy import URL

from . import environments

ConfigMap = dict[str, dict[str, str | int | bool | float]]

log = structlog.get_logger(__name__)


class Env(StrEnum):
    DEV = auto()
    PROD = auto()
    # Sample environment to ensure config values even though
    # they are not real
    SAMPLE = auto()


class MetricsEnv(StrEnum):
    # The values are obtained from the EMF configuration. We are using CLOUDWATCH and STDOUT to make it clear
    # what are we using but they are referred to as default and local
    # See: https://drp.li/SZQCc
    CLOUDWATCH = "default"
    STDOUT = "local"
    RICH = "rich"


class RunModes(StrEnum):
    POLLING = auto()
    WEBHOOK = auto()


class ConfigProvider(Protocol):
    """Protocol for a configuration provider"""

    def get_config(self) -> ConfigMap: ...


@dataclass
class TomlConfigProvider:
    """
    The TomlConfigProvider will parse the configuration file corresponding to the
    environment `env` and generate a configuration map where they keys are the
    TOML sections found in the configuration file. The value associated with each key
    is a set of key-value pairs for each configuration option in that section.

    The configuration files are stored under `environments/<env>.toml`. Each environment
    file can have any set of configuration needed.
    """

    env: Env

    def get_config(self) -> ConfigMap:
        config_file = f"{self.env.value}.toml"
        try:
            resource = files(environments) / config_file
            with as_file(resource) as config_path:
                return self.__process_config_file(config_path)
        except Exception as exc:
            # If there is an error reading the config file we just log a warning and
            # return no content. Data validation by the Config will piont out the issue
            log.warning("Could not read configuration file", config_file=config_file, exc_info=exc)
            return {}

    def __process_config_file(self, config_path: Path) -> ConfigMap:
        with open(config_path) as config_file:
            return tomllib.loads(config_file.read())


class EnvVariablesConfigProvider:
    """
    This config provider will generate the config from environment variables with the following
    naming convention:

    MITUPBOT__<GROUP>__<KEY> = <VALUE>

    Any environment variable that starts with MITUPBOT will be considered. The config group
    and configuration key are both separated by 2 underscored.

    Every token will be converted to snake case.
    """

    def get_config(self) -> ConfigMap:
        config: ConfigMap = {}
        for variable, value in os.environ.items():
            if variable.startswith("MITUPBOT__"):
                _, group, key = variable.split("__")
                config.setdefault(group.lower(), {})[key.lower()] = self.__convert_value(value)
        return config

    def __convert_value(self, value: str) -> str | bool | int | float:
        # Handle boolean as it is a bit different
        if (low := value.lower()) in {"true", "false"}:
            return low == "true"

        # Check if we can convert to other types
        for t in (int, float):
            with contextlib.suppress(ValueError):
                # Expected value error when trying to convert if we cannot. Suppresing
                # to ignore
                return t(value)
        # If we are still here it means we have failed to convert, keep as string
        return value


class DbConfig(BaseModel):
    username: str
    password: SecretStr
    url: str
    database: str
    port: int = 5432
    # psycopg (v3) drives both the async bot engine and Alembic's sync migration engine.
    url_schema: str = "postgresql+psycopg"
    engine_echo: bool = False
    # Connection pool sizing for the bot engine. Defaults mirror SQLAlchemy's own
    # (5 persistent + 10 overflow), which is the capacity the bot ran with before
    # the pool was made explicit.
    pool_size: int = 5
    max_overflow: int = 10
    pool_timeout: int = 30

    @property
    def full_url(self) -> URL:
        secret_pass = self.password.get_secret_value()
        return URL.create(
            drivername=self.url_schema,
            username=self.username,
            password=secret_pass,
            host=self.url,
            port=self.port,
            database=self.database,
        )


class AppConfig(BaseModel):
    run_mode: RunModes
    log_level: str = "INFO"

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in logging.getLevelNamesMapping():
            raise ValueError(f"Invalid log level: {value!r}")
        return normalized


class BotConfig(BaseModel):
    token: SecretStr
    # These properties are needed when running with webhook
    domain: str | None = None
    # Port where Telegram should connect to. This is not the port in the host
    # where the bot listens
    port: int = 443
    # Port on which the uvicorn server listens inside the container. Defaults to 80
    # to match the ECS task containerPort without requiring any infra change.
    listen_port: int = 80
    # Secret token provided to Telegram to validate connections
    secret_token: SecretStr | None = None
    max_connections: int = 100
    retries_on_throttle: int = 3
    # Cap on updates the PTB application processes concurrently. 1 keeps update handling
    # strictly sequential; raising it is the deliberate concurrency flip (#190), done via
    # env var override at rollout time so the revert stays config-only.
    concurrent_updates: int = Field(default=1, ge=1)


class GoogleApiConfig(BaseModel):
    gmaps_geocode_key: SecretStr
    gmaps_timezone_key: SecretStr


class MetricsConfig(BaseModel):
    # Even though EMF configuration can be done through environment variables, we are
    # keeping it here for clarity and managing it ourselves
    namespace: str
    environment: MetricsEnv
    flush_on_emission: bool = False


# Connections kept free for work that runs outside update handlers: the job queue and the
# post-fan-out reconcile transactions must never find the pool fully claimed by handlers.
POOL_CONNECTION_HEADROOM = 2


class Config(BaseModel):
    """
    MitupRuntime configuration.

    Config is a pydantic model that is generated merging configuration vlues from different
    sources depending on the configuration suppliers provided through the `from_providers`
    method. The config object should be generated only using the `from_providers` method.

    If a variable is found defined by multiple providers, the provider order specifiy
    the priority. The configuration value supplied by the first provider where the
    option is available will be the one used.

    Once the the configuration keys mapping has been found it is then used to build the
    pydantic model that validates configuration data and fills approrpiate default
    values.
    """

    db: DbConfig
    bot: BotConfig
    google_api: GoogleApiConfig
    app: AppConfig
    metrics: MetricsConfig

    @model_validator(mode="after")
    def validate_concurrency_fits_pool(self) -> Config:
        """Fail at boot when the update-concurrency cap could exhaust the connection pool —
        a misconfigured cap must be a startup error, not a runtime pool-timeout mystery."""
        connection_budget = self.db.pool_size + self.db.max_overflow - POOL_CONNECTION_HEADROOM
        if self.bot.concurrent_updates > connection_budget:
            raise ValueError(
                f"bot.concurrent_updates ({self.bot.concurrent_updates}) exceeds the connection budget: "
                f"db.pool_size ({self.db.pool_size}) + db.max_overflow ({self.db.max_overflow}) "
                f"- {POOL_CONNECTION_HEADROOM} headroom for the job queue and reconcile transactions "
                f"= {connection_budget}. Raise the pool sizing or lower the cap."
            )
        return self

    @staticmethod
    def from_providers(*providers: ConfigProvider) -> Config:
        data: ConfigMap = {}

        for provider in reversed(providers):
            provider_config = provider.get_config()
            for group, config in provider_config.items():
                data.setdefault(group, {})
                data[group] |= config

        return Config.model_validate(data)
