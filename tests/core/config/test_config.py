from typing import cast
from unittest import mock

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr, ValidationError
from sqlalchemy import URL

from mitup_bot import db
from mitup_bot.config import (
    AppConfig,
    BotConfig,
    Config,
    DbConfig,
    Env,
    EnvVariablesConfigProvider,
    GoogleApiConfig,
    MetricsConfig,
    MetricsEnv,
    PatreonConfig,
    RunModes,
    TomlConfigProvider,
)
from mitup_bot.models.subscriptions import TokenCipher, configure_token_encryption

TOML_CONTENT = """
[db]
username = "username"
password = "xxxxx"
url      = "some.url.com"
database = "mydb"
port     = 12

[app]
run_mode = "polling"

[metrics]
namespace = "MitupBot"
environment = "local"
log_group = "LogGroup"
"""

CONFIG_FROM_ENV = {
    "db": {
        "password": "1234abc",
    },
    "bot": {
        "token": "abcd12345",
    },
    "google_api": {
        "gmaps_geocode_key": "1a2b3c45d6e7f8g",
        "gmaps_timezone_key": "9h0i1j2k3l4m5n6o",
    },
}

FULL_PATREON_TOML_SECTION = """
[patreon]
client_id = "patreon-client-abc"
client_secret = "patreon-secret-xyz"
campaign_id = "1234567"
redirect_uri = "https://bot.example/patreon/callback"
creator_access_token = "seed-access-token"
creator_refresh_token = "seed-refresh-token"
state_secret = "fernet-state-key"
encryption_key = "fernet-encryption-key"
"""

PARTIAL_PATREON_TOML_SECTION = """
[patreon]
client_id = "patreon-client-abc"
client_secret = "patreon-secret-xyz"
"""

EMPTY_PATREON_TOML_SECTION = """
[patreon]
"""

FULL_PATREON_ENV_SECTION = {
    "patreon": {
        "client_id": "patreon-client-abc",
        "client_secret": "patreon-secret-xyz",
        # Numeric on purpose: the env provider hands this back as an int, exercising the
        # coerce_numbers_to_str path on PatreonConfig.
        "campaign_id": "1234567",
        "redirect_uri": "https://bot.example/patreon/callback",
        "creator_access_token": "seed-access-token",
        "creator_refresh_token": "seed-refresh-token",
        "state_secret": "fernet-state-key",
        "encryption_key": "fernet-encryption-key",
    },
}

BROKEN_TOML_CONTENT = """
[db]
url      = "some.url.com"
database = "mydb"
port     = 12

[app]
run_mode = "polling"

[bot]
token = "123123123"

[google_api]
gmaps_geocode_key = "1a2b3c45d6e7f8g"

[metrics]
environment = "broken"
namespace = "MitupBot"
"""


@pytest.mark.parametrize(
    "mock_toml_config,mock_env_config",
    ([TOML_CONTENT, CONFIG_FROM_ENV],),
    indirect=True,
    ids=["working_config"],
)
def test_config_properly_setup(
    mock_toml_config: tuple[mock.Mock],
    mock_env_config: None,
):
    # Given a valid configuration set from toml and environment we can use the providers to get a config object
    config = Config.from_providers(EnvVariablesConfigProvider(), TomlConfigProvider(Env.DEV))

    # Password from environment variable takes precedence as it is defined before Toml
    assert config.db.username == "username"
    assert config.db.password.get_secret_value() == "1234abc"
    assert config.db.url == "some.url.com"
    assert config.db.port == 12
    assert config.db.database == "mydb"
    # End to end: the provider-merged DbConfig assembles the expected DSN through the engine layer.
    assert db.build_db_url(config.db) == URL.create(
        drivername="postgresql+psycopg",
        username="username",
        password="1234abc",
        host="some.url.com",
        port=12,
        database="mydb",
    )
    assert config.bot.token.get_secret_value() == "abcd12345"
    assert config.google_api.gmaps_geocode_key.get_secret_value() == "1a2b3c45d6e7f8g"
    assert config.google_api.gmaps_timezone_key.get_secret_value() == "9h0i1j2k3l4m5n6o"
    assert RunModes.POLLING is config.app.run_mode


@pytest.mark.parametrize("mock_toml_config", (BROKEN_TOML_CONTENT,), indirect=True, ids=["broken_config"])
def test_config_fails_with_missing_values(mock_toml_config: tuple[mock.Mock]):
    # We are just supplying a broken toml where the information for db is not complete.
    # A validation error should be raised
    with pytest.raises(ValidationError) as exc_info:
        Config.from_providers(EnvVariablesConfigProvider(), TomlConfigProvider(Env.DEV))

    # Assert that there are 2 errors for the missing pieces of db
    assert len(exc_info.value.errors()) == 4
    assert exc_info.value.errors()[0]["type"] == "missing"
    assert exc_info.value.errors()[1]["type"] == "missing"
    assert exc_info.value.errors()[2]["type"] == "missing"
    assert exc_info.value.errors()[3]["type"] == "enum"
    assert exc_info.value.errors()[0]["loc"] == ("db", "username")
    assert exc_info.value.errors()[1]["loc"] == ("db", "password")
    assert exc_info.value.errors()[2]["loc"] == ("google_api", "gmaps_timezone_key")
    assert exc_info.value.errors()[3]["loc"] == ("metrics", "environment")

    assert exc_info.value.title == "Config"


def build_config(*, concurrent_updates: int = 1, pool_size: int = 5, max_overflow: int = 10) -> Config:
    return Config(
        db=DbConfig(
            username="user",
            password=SecretStr("password"),
            url="testhost",
            database="db",
            pool_size=pool_size,
            max_overflow=max_overflow,
        ),
        bot=BotConfig(token=SecretStr("fake-bot-token"), concurrent_updates=concurrent_updates),
        google_api=GoogleApiConfig(
            gmaps_geocode_key=SecretStr("geocode-key"),
            gmaps_timezone_key=SecretStr("timezone-key"),
        ),
        app=AppConfig(run_mode=RunModes.POLLING),
        metrics=MetricsConfig(namespace="test", environment=MetricsEnv.STDOUT),
    )


def test_bot_config_concurrent_updates_defaults_to_sequential():
    config = BotConfig(token=SecretStr("fake-bot-token"))

    assert config.concurrent_updates == 1


def test_bot_config_concurrent_updates_must_be_positive():
    with pytest.raises(ValidationError) as exc_info:
        BotConfig(token=SecretStr("fake-bot-token"), concurrent_updates=0)

    assert exc_info.value.errors()[0]["loc"] == ("concurrent_updates",)


def test_bot_config_broadcast_max_rate_defaults_to_five():
    config = BotConfig(token=SecretStr("fake-bot-token"))

    assert config.broadcast_max_rate == 5


@pytest.mark.parametrize(
    "raw,expected",
    [
        pytest.param(12, 12, id="native_int"),
        pytest.param("9", 9, id="numeric_string_from_env"),
    ],
)
def test_bot_config_broadcast_max_rate_override_parses(raw: object, expected: int):
    # The env provider delivers overrides as ints or numeric strings; both must land as int.
    config = BotConfig(token=SecretStr("fake-bot-token"), broadcast_max_rate=cast(int, raw))

    assert config.broadcast_max_rate == expected


def test_bot_config_hosts_group_defaults_to_disabled():
    config = BotConfig(token=SecretStr("fake-bot-token"))

    assert config.hosts_group_chat_id is None
    assert config.hosts_group_invite_url is None


def test_bot_config_hosts_group_values_are_kept():
    config = BotConfig(
        token=SecretStr("fake-bot-token"),
        hosts_group_chat_id=-1001234567890,
        hosts_group_invite_url="https://t.me/+abcdef",
    )

    assert config.hosts_group_chat_id == -1001234567890
    assert config.hosts_group_invite_url == "https://t.me/+abcdef"


def test_bot_config_admin_tg_ids_defaults_to_empty():
    config = BotConfig(token=SecretStr("fake-bot-token"))

    assert config.admin_tg_ids == []


@pytest.mark.parametrize(
    "raw,expected",
    [
        pytest.param([123, 456], [123, 456], id="native_toml_array"),
        pytest.param(123456789, [123456789], id="single_int_from_env"),
        pytest.param("123,456", [123, 456], id="comma_separated_string"),
        pytest.param(" 123 , 456 ", [123, 456], id="comma_separated_with_spaces"),
        pytest.param("[123, 456]", [123, 456], id="json_array_string"),
        pytest.param("123456789", [123456789], id="single_id_string"),
        pytest.param("", [], id="empty_string"),
        pytest.param([], [], id="empty_list"),
    ],
)
def test_bot_config_admin_tg_ids_coercion(raw: object, expected: list[int]):
    # The before-validator coerces every provider shape (list, int, comma/JSON string) into a
    # list[int]; cast documents that we deliberately feed it a non-list to exercise that path.
    config = BotConfig(token=SecretStr("fake-bot-token"), admin_tg_ids=cast(list[int], raw))

    assert config.admin_tg_ids == expected


def test_bot_config_admin_tg_ids_rejects_non_numeric():
    with pytest.raises(ValidationError) as exc_info:
        BotConfig(token=SecretStr("fake-bot-token"), admin_tg_ids=cast(list[int], "abc,def"))

    assert exc_info.value.errors()[0]["loc"][0] == "admin_tg_ids"


def test_concurrency_cap_at_connection_budget_accepted():
    # pool_size 10 + max_overflow 5 - 2 headroom = 13: the largest cap that boots.
    config = build_config(concurrent_updates=13, pool_size=10, max_overflow=5)

    assert config.bot.concurrent_updates == 13


def test_concurrency_cap_exceeding_connection_budget_rejected():
    with pytest.raises(ValidationError, match="exceeds the connection budget"):
        build_config(concurrent_updates=14, pool_size=10, max_overflow=5)


def test_app_config_log_level_defaults_to_info():
    config = AppConfig(run_mode=RunModes.POLLING)

    assert config.log_level == "INFO"


def test_app_config_log_level_normalized_to_upper():
    config = AppConfig(run_mode=RunModes.POLLING, log_level="debug")

    assert config.log_level == "DEBUG"


def test_app_config_invalid_log_level_raises():
    with pytest.raises(ValidationError) as exc_info:
        AppConfig(run_mode=RunModes.POLLING, log_level="bogus")

    error = exc_info.value.errors()[0]
    assert error["loc"] == ("log_level",)


@pytest.mark.parametrize(
    "mock_toml_config,mock_env_config",
    ([TOML_CONTENT, CONFIG_FROM_ENV],),
    indirect=True,
    ids=["working_config"],
)
def test_config_without_log_level_still_builds(
    mock_toml_config: tuple[mock.Mock],
    mock_env_config: None,
):
    # Backwards-compat: TOML_CONTENT does not set [app] log_level, so the default must apply.
    config = Config.from_providers(EnvVariablesConfigProvider(), TomlConfigProvider(Env.DEV))

    assert config.app.log_level == "INFO"


@pytest.mark.parametrize(
    "mock_toml_config,mock_env_config",
    ([TOML_CONTENT, CONFIG_FROM_ENV],),
    indirect=True,
    ids=["patreon_absent"],
)
def test_config_without_patreon_section_is_none(
    mock_toml_config: tuple[mock.Mock],
    mock_env_config: None,
):
    # The Patreon section is optional; the bot must boot with it entirely absent.
    config = Config.from_providers(EnvVariablesConfigProvider(), TomlConfigProvider(Env.DEV))

    assert config.patreon is None


@pytest.mark.parametrize(
    "mock_toml_config,mock_env_config",
    ([TOML_CONTENT + FULL_PATREON_TOML_SECTION, CONFIG_FROM_ENV],),
    indirect=True,
    ids=["patreon_via_toml"],
)
def test_full_patreon_section_from_toml_is_parsed(
    mock_toml_config: tuple[mock.Mock],
    mock_env_config: None,
):
    config = Config.from_providers(EnvVariablesConfigProvider(), TomlConfigProvider(Env.DEV))

    assert config.patreon is not None
    assert config.patreon.client_id == "patreon-client-abc"
    assert config.patreon.campaign_id == "1234567"
    assert config.patreon.redirect_uri == "https://bot.example/patreon/callback"
    assert config.patreon.client_secret.get_secret_value() == "patreon-secret-xyz"
    assert config.patreon.creator_access_token.get_secret_value() == "seed-access-token"
    assert config.patreon.creator_refresh_token.get_secret_value() == "seed-refresh-token"
    assert config.patreon.state_secret.get_secret_value() == "fernet-state-key"
    assert config.patreon.encryption_key.get_secret_value() == "fernet-encryption-key"


@pytest.mark.parametrize(
    "mock_toml_config,mock_env_config",
    ([TOML_CONTENT, {**CONFIG_FROM_ENV, **FULL_PATREON_ENV_SECTION}],),
    indirect=True,
    ids=["patreon_via_env"],
)
def test_full_patreon_section_from_env_is_parsed(
    mock_toml_config: tuple[mock.Mock],
    mock_env_config: None,
):
    config = Config.from_providers(EnvVariablesConfigProvider(), TomlConfigProvider(Env.DEV))

    assert config.patreon is not None
    # campaign_id arrives as an int from the env provider and is coerced back to str.
    assert config.patreon.campaign_id == "1234567"
    assert config.patreon.client_id == "patreon-client-abc"
    assert config.patreon.client_secret.get_secret_value() == "patreon-secret-xyz"
    assert config.patreon.encryption_key.get_secret_value() == "fernet-encryption-key"


@pytest.mark.parametrize(
    "mock_toml_config,mock_env_config",
    ([TOML_CONTENT + EMPTY_PATREON_TOML_SECTION, CONFIG_FROM_ENV],),
    indirect=True,
    ids=["patreon_empty_section"],
)
def test_empty_patreon_section_is_treated_as_absent(
    mock_toml_config: tuple[mock.Mock],
    mock_env_config: None,
):
    # An empty [patreon] header (the preemptive-section-with-env-overrides pattern) must read
    # as absent rather than firing eight "field required" errors at boot.
    config = Config.from_providers(EnvVariablesConfigProvider(), TomlConfigProvider(Env.DEV))

    assert config.patreon is None


@pytest.mark.parametrize(
    "mock_toml_config,mock_env_config",
    ([TOML_CONTENT + PARTIAL_PATREON_TOML_SECTION, CONFIG_FROM_ENV],),
    indirect=True,
    ids=["partial_patreon_toml"],
)
def test_partial_patreon_section_from_toml_raises(
    mock_toml_config: tuple[mock.Mock],
    mock_env_config: None,
):
    # A partial section falls through to pydantic, which reports each missing field by name.
    with pytest.raises(ValidationError) as exc_info:
        Config.from_providers(EnvVariablesConfigProvider(), TomlConfigProvider(Env.DEV))

    missing_locations = {error["loc"] for error in exc_info.value.errors() if error["type"] == "missing"}
    assert ("patreon", "campaign_id") in missing_locations
    assert ("patreon", "encryption_key") in missing_locations
    assert ("patreon", "creator_access_token") in missing_locations


@pytest.mark.parametrize(
    "mock_toml_config,mock_env_config",
    ([TOML_CONTENT, {**CONFIG_FROM_ENV, "patreon": {"client_id": "patreon-client-abc"}}],),
    indirect=True,
    ids=["partial_patreon_env"],
)
def test_partial_patreon_section_from_env_raises(
    mock_toml_config: tuple[mock.Mock],
    mock_env_config: None,
):
    # A lone MITUPBOT__PATREON__CLIENT_ID env var lands in the same partial-dict shape as a
    # partial TOML block, so pydantic rejects it the same way.
    with pytest.raises(ValidationError) as exc_info:
        Config.from_providers(EnvVariablesConfigProvider(), TomlConfigProvider(Env.DEV))

    missing_locations = {error["loc"] for error in exc_info.value.errors() if error["type"] == "missing"}
    assert ("patreon", "client_secret") in missing_locations


def test_patreon_secrets_are_masked_in_repr():
    patreon = PatreonConfig(
        client_id="patreon-client-abc",
        client_secret=SecretStr("super-secret"),
        campaign_id="1234567",
        redirect_uri="https://bot.example/patreon/callback",
        creator_access_token=SecretStr("seed-access-token"),
        creator_refresh_token=SecretStr("seed-refresh-token"),
        state_secret=SecretStr("fernet-state-key"),
        encryption_key=SecretStr("fernet-encryption-key"),
    )

    rendered = f"{patreon!r}{patreon}"

    secrets = (
        "super-secret",
        "seed-access-token",
        "seed-refresh-token",
        "fernet-state-key",
        "fernet-encryption-key",
    )
    for secret in secrets:
        assert secret not in rendered
    # Non-secret fields stay visible for debugging.
    assert "patreon-client-abc" in rendered


def make_patreon_config(encryption_key: str) -> PatreonConfig:
    return PatreonConfig(
        client_id="patreon-client-abc",
        client_secret=SecretStr("super-secret"),
        campaign_id="1234567",
        redirect_uri="https://bot.example/patreon/callback",
        creator_access_token=SecretStr("seed-access-token"),
        creator_refresh_token=SecretStr("seed-refresh-token"),
        state_secret=SecretStr("fernet-state-key"),
        encryption_key=SecretStr(encryption_key),
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("single-key", ["single-key"]),
        ("new-key,old-key", ["new-key", "old-key"]),
        ("new-key old-key", ["new-key", "old-key"]),
        ("  new-key , old-key ,, ", ["new-key", "old-key"]),
        ("new-key,\n old-key", ["new-key", "old-key"]),
    ],
)
def test_patreon_encryption_keys_parsing(raw: str, expected: list[str]):
    assert make_patreon_config(raw).encryption_keys() == expected


@pytest.mark.parametrize("raw", ["", "   ", " , , "])
def test_patreon_encryption_key_rejects_no_usable_key(raw: str):
    with pytest.raises(ValidationError, match="at least one Fernet key"):
        make_patreon_config(raw)


def test_patreon_encryption_keys_wire_a_rotation():
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()

    configure_token_encryption(old_key)
    legacy_ciphertext = TokenCipher.encrypt("token")

    config = make_patreon_config(f"{new_key},{old_key}")
    configure_token_encryption(*config.encryption_keys())

    # A token written under the old key still decrypts after the rotation config is applied.
    assert TokenCipher.decrypt(legacy_ciphertext) == "token"
    # Fresh writes use the new primary key.
    rotated_ciphertext = TokenCipher.encrypt("token")
    assert Fernet(new_key).decrypt(rotated_ciphertext.encode()).decode() == "token"


def provider_returning(data: dict[str, dict[str, object]]) -> mock.Mock:
    provider = mock.Mock()
    provider.get_config.return_value = data
    return provider


def test_db_from_providers_builds_only_the_db_section():
    provider = provider_returning(
        {"db": {"username": "someone", "password": "secret", "url": "localhost", "database": "mitup"}}
    )

    db_config = Config.db_from_providers(provider)

    assert db_config.username == "someone"
    assert db_config.password.get_secret_value() == "secret"
    assert db_config.port == 5432


def test_db_from_providers_first_provider_wins():
    override = provider_returning({"db": {"password": "winner"}})
    base = provider_returning(
        {"db": {"username": "someone", "password": "loser", "url": "localhost", "database": "mitup"}}
    )

    assert Config.db_from_providers(override, base).password.get_secret_value() == "winner"


def test_db_from_providers_missing_section_raises_with_setup_hint():
    with pytest.raises(RuntimeError, match="uv run mb setup"):
        Config.db_from_providers(provider_returning({}))
