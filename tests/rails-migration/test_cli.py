import re
from unittest.mock import AsyncMock, MagicMock, patch

import sqlalchemy.exc
from click.testing import CliRunner
from pydantic import SecretStr

from mitup_bot.config import DbConfig
from mitup_bot.migration.cli import cli, rails_connection_target

ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Drop rich's colour codes so assertions match the plain text, not the styling."""
    return ANSI_ESCAPE.sub("", text)


def test_rails_connection_target_parses_uri_and_redacts_password():
    dsn = "postgres://alice:s3cret@rails.example.internal:5432/mitup_production"
    target = rails_connection_target(dsn)

    assert target == {
        "host": "rails.example.internal",
        "port": "5432",
        "dbname": "mitup_production",
        "user": "alice",
    }
    assert "s3cret" not in repr(target)


def test_rails_connection_target_parses_keyword_dsn():
    dsn = "host=rails.example.internal port=5432 dbname=mitup_production user=alice password=s3cret"
    target = rails_connection_target(dsn)

    assert target["host"] == "rails.example.internal"
    assert target["user"] == "alice"
    assert "password" not in target
    assert "s3cret" not in repr(target)


def test_target_db_operational_error_aborts_cleanly():
    config = MagicMock()
    config.db = DbConfig(
        username="user",
        password=SecretStr("password"),
        url="target.example.internal",
        database="mitup",
        port=5432,
    )

    with (
        patch("mitup_bot.migration.cli.Config.from_providers", return_value=config),
        patch("mitup_bot.migration.cli.db.configure_db"),
        patch("mitup_bot.migration.cli.configure_emf_backend"),
        patch(
            "mitup_bot.migration.cli.run_pipeline_then_flush",
            new_callable=AsyncMock,
            side_effect=sqlalchemy.exc.OperationalError("connect", {}, Exception("timeout")),
        ),
    ):
        result = CliRunner().invoke(
            cli, ["migrate", "--rails-url", "postgres://alice:s3cret@rails.internal:5432/rails"]
        )

    output = strip_ansi(result.output)
    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "target.example.internal:5432" in output
    assert "s3cret" not in output
