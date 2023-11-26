import datetime as dt
from unittest import mock

from freezegun import freeze_time

from mitup_bot.models import User

MOCKED_UTC_NOW = dt.datetime(2023, 11, 20, 12, 12, tzinfo=dt.timezone.utc)


def test_user_is_created(mock_session: mock.MagicMock):
    user = User(
        tg_user_id=123456,
        first_name="myname",
        last_name="lastname",
        username="username",
    )
    user.create()

    # Add and commit has been called
    mock_session.add.assert_called_with(user)
    mock_session.commit.assert_called_once()


@freeze_time(MOCKED_UTC_NOW)
def test_user_is_updated_with_updated_update_time(mock_session: mock.MagicMock):
    user = User(
        tg_user_id=123456,
        first_name="myname",
        last_name="lastname",
        username="username",
    )
    user.update()

    # Add and commit has been called
    mock_session.add.assert_called_once()
    mock_session.commit.assert_called_once()

    # The updated_time of the user used as first argument on the first call is now
    added_user: User = mock_session.add.call_args_list[0].args[0]
    assert MOCKED_UTC_NOW == added_user.updated_time
