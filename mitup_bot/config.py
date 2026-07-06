from __future__ import annotations

import contextlib
import json
import logging
import os
import tomllib
from dataclasses import dataclass
from enum import StrEnum, auto
from importlib.resources import as_file, files
from pathlib import Path
from typing import Any, Protocol

import structlog
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
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
    # (5 persistent + 10 overflow).
    pool_size: int = 5
    max_overflow: int = 10
    pool_timeout: int = 30
    # Emit connection-pool metrics (in-use gauge, checkout wait time, pool-timeout counter) from
    # the process that owns this engine. Off by default so CLI tools and tests keep an
    # uninstrumented pool; the bot and events services enable it via env-var override.
    pool_metrics_enabled: bool = False

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
    # strictly sequential; raising it is the deliberate concurrency flip, done via
    # env var override at rollout time so the revert stays config-only.
    concurrent_updates: int = Field(default=1, ge=1)
    # Telegram user ids allowed to operate the mass-broadcast feature. Empty by default, which
    # keeps the feature dormant until ids are set. In production it is set via env var override
    # (MITUPBOT__BOT__ADMIN_TG_IDS) so the allowlist stays out of the checked-in TOML.
    admin_tg_ids: list[int] = Field(default_factory=list)

    @field_validator("admin_tg_ids", mode="before")
    @classmethod
    def parse_admin_tg_ids(cls, value: object) -> object:
        """Coerce the allowlist from every shape a config provider can deliver.

        A native TOML array arrives already as a list. The env provider collapses a single
        `MITUPBOT__BOT__ADMIN_TG_IDS` var to an int (one id), a comma-separated string, or a
        JSON array string, so all three must fold back to a list before `list[int]` validation.
        """
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, int):
            return [value]
        if isinstance(value, str):
            if not (text := value.strip()):
                return []
            with contextlib.suppress(json.JSONDecodeError):
                parsed = json.loads(text)
                return parsed if isinstance(parsed, list) else [parsed]
            return [part.strip() for part in text.split(",") if part.strip()]
        return value


class GoogleApiConfig(BaseModel):
    gmaps_geocode_key: SecretStr
    gmaps_timezone_key: SecretStr


class MetricsConfig(BaseModel):
    # Even though EMF configuration can be done through environment variables, we are
    # keeping it here for clarity and managing it ourselves
    namespace: str
    environment: MetricsEnv
    flush_on_emission: bool = False


class LimitsConfig(BaseModel):
    """Free-tier limits that the Patron tier has raised (see `mitup_bot.supporter` for the level ->
    cap policy; the Organizer tier skips these caps entirely).

    Every field is defaulted so the limits ship without any TOML or env change: the code default
    is the live value until a deploy tunes it through a `MITUPBOT__LIMITS__*` override, keeping the
    revert config-only. The Patron values are deliberately generous sanity caps, not real limits.
    """

    # Active meetings a user may own at once (enforced at meeting creation and reactivation).
    free_active_meetings: int = 5
    patron_active_meetings: int = 50
    # How many days ahead a meeting's start date may be set. The free horizon stays wide enough
    # for normal planning (a dinner a couple of months out); the limit only targets meetings so
    # far in the future they clog the database, which is exactly what supporters offset.
    free_scheduling_horizon_days: int = 90
    patron_scheduling_horizon_days: int = 365
    # Free-tier per-meeting participant capacity, enforced as an effective cap on joins and
    # waiting-list promotions (a free owner's "no limit" resolves to this value). Patron and
    # Organizer owners are uncapped. Participant rows are a direct DB-growth lever, which is the
    # cost supporters offset.
    free_participant_capacity: int = 20


class PatreonConfig(BaseModel):
    """Patreon OAuth integration credentials.

    Wired into `Config` as an optional section: the bot boots without a `[patreon]` block. Every
    field is required, so a partially-filled section fails validation with pydantic's per-field
    "field required" errors.
    """

    # Numeric env-var values arrive as int (the env provider coerces digit-only strings);
    # Patreon campaign/client IDs are numeric, so coerce them back to str instead of failing.
    model_config = ConfigDict(coerce_numbers_to_str=True)

    client_id: str
    client_secret: SecretStr
    campaign_id: str
    redirect_uri: str
    # Seed value only. The daily refresh job persists the live creator token pair in
    # the DB, which is the source of truth after the first refresh — this seed is
    # re-adopted only when its value changes (fingerprint comparison), so rotating the
    # credential is just replacing the CI/SSM variable.
    creator_access_token: SecretStr
    # Seed value only, adopted together with `creator_access_token` (same fingerprint check).
    creator_refresh_token: SecretStr
    # Fernet key for the OAuth `state` parameter (carries the initiating tg_user_id, age-validated
    # against `oauth.STATE_TTL_SECONDS`).
    state_secret: SecretStr
    # Fernet key(s) for encrypting Patreon tokens at rest in the DB. Kept separate from
    # `state_secret` so the two keys have a different blast radius. Accepts one or more keys
    # separated by commas and/or whitespace: the first is primary (encrypts new writes), the
    # rest only decrypt, so an operator sets `new,old` to rotate (see `encryption_keys`).
    encryption_key: SecretStr
    # Per-request timeout (seconds) for the httpx client that talks to the Patreon API. Optional
    # with a sensible default so operators can tune it without a code change if Patreon is slow.
    request_timeout_seconds: float = 30.0
    # Minimum `currently_entitled_amount_cents` (VAT-exclusive pledge base) an active member must
    # reach for each supporter tier, matching the live €3/€5/€10 campaign tiers. A member's level is
    # the highest threshold their entitled amount reaches (see `mitup_bot.supporter.level_for_amount`);
    # an active member below the lowest threshold still counts as SUPPORTER. Defaulted so tuning a
    # tier price is a config-only change.
    supporter_min_cents: int = 300
    patron_min_cents: int = 500
    organizer_min_cents: int = 1000

    @staticmethod
    def parse_encryption_keys(raw: str) -> list[str]:
        """Split a raw `encryption_key` value into ordered Fernet keys.

        Fernet keys are urlsafe base64, so treating commas as whitespace and splitting keeps a
        lone key intact while yielding `[new, old]` for a rotation value.
        """
        return raw.replace(",", " ").split()

    @field_validator("encryption_key", mode="after")
    @classmethod
    def validate_encryption_key(cls, value: SecretStr) -> SecretStr:
        if not cls.parse_encryption_keys(value.get_secret_value()):
            raise ValueError("encryption_key must contain at least one Fernet key")
        return value

    def encryption_keys(self) -> list[str]:
        """Ordered Fernet keys; first is primary (encrypts writes), all decrypt."""
        return self.parse_encryption_keys(self.encryption_key.get_secret_value())


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
    # Every field is defaulted, so the section is always present without any TOML entry.
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    # First optional section: Patreon may be absent entirely during rollout. When present, every
    # field is required, so pydantic rejects a partial section on its own.
    patreon: PatreonConfig | None = None

    @model_validator(mode="before")
    @classmethod
    def treat_empty_patreon_section_as_absent(cls, data: Any) -> Any:
        """Drop an empty `[patreon]` section so the optional `None` default applies.

        This repo's TOML style ships empty sections that are filled by `MITUPBOT__*` env-var
        overrides at deploy time (prod.toml carries an empty `[bot]` today). A preemptive empty
        `[patreon]` header must therefore read as "absent", not as a fully-unset section — which
        would otherwise fail with eight "field required" errors at boot. A partial section still
        falls through to pydantic, which reports each missing key by name.
        """
        if not isinstance(data, dict):
            return data
        patreon = data.get("patreon")
        if isinstance(patreon, dict) and not patreon:
            return {key: value for key, value in data.items() if key != "patreon"}
        return data

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
