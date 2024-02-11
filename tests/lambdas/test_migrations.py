from unittest import mock

import pytest
from pydantic import ValidationError

from mitup_bot.lambdas.migrations import run_migrations


def test_upgrade():
    with mock.patch("mitup_bot.lambdas.migrations.command") as mock_command:
        with mock.patch("mitup_bot.lambdas.migrations.Config") as mock_alembic_config:
            event = {"action": "upgrade", "revision": "myRevision"}
            alembic_config = mock.MagicMock(name="AlembicConfigMock")
            mock_alembic_config.return_value = alembic_config

            run_migrations(event, None)

            mock_command.upgrade.assert_called_once_with(alembic_config, "myRevision")
            mock_command.downgrade.assert_not_called()


def test_downgrade():
    with mock.patch("mitup_bot.lambdas.migrations.command") as mock_command:
        with mock.patch("mitup_bot.lambdas.migrations.Config") as mock_alembic_config:
            event = {"action": "downgrade", "revision": "myRevision"}
            alembic_config = mock.MagicMock(name="AlembicConfigMock")
            mock_alembic_config.return_value = alembic_config

            run_migrations(event, None)

            mock_command.downgrade.assert_called_once_with(alembic_config, "myRevision")
            mock_command.upgrade.assert_not_called()


def test_wrong_event_fails():
    event = {"action": "myAction", "revision": "myRevision"}

    with pytest.raises(ValidationError) as exc_info:
        run_migrations(event, None)

    assert 1 == exc_info.value.error_count()

    error = exc_info.value.errors()[0]
    # Validate that there was an error parsing action, as it is not recognized in the enum
    assert "enum" == error["type"]
    assert "action" == error["loc"][0]
    assert "myAction" == error["input"]
