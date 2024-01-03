import datetime as dt
from unittest import mock

from mitup_bot.models import User, Settings

MOCKED_UTC_NOW = dt.datetime(2023, 11, 20, 12, 12, tzinfo=dt.timezone.utc)


def test_settings_is_created(mock_session: mock.MagicMock):
    user = User(
        id=1,
        tg_user_id=123456,
        first_name="myname",
        last_name="lastname",
        username="username",
    )
    settings = Settings(
        languaje="es",
        timezone="Jaen",
        user=user,
    )

    settings.create()

    # Add and commit has been called
    mock_session.add.assert_called_with(settings)
    mock_session.commit.assert_called_once()


def test_settings_is_updated_with_updated_update_time(mock_session: mock.MagicMock):
    user = User(
        id=1,
        tg_user_id=123456,
        first_name="myname",
        last_name="lastname",
        username="username",
    )

    settings = Settings(
        languaje="es",
        timezone="Jaen",
        user=user,
    )

    settings.update()

    # Add and commit has been called
    mock_session.add.assert_called_once()
    mock_session.commit.assert_called_once()

    # The updated_time of the settings used as first argument on the first call is now
    added_settings: Settings = mock_session.add.call_args_list[0].args[0]
    assert MOCKED_UTC_NOW == added_settings.updated_time
