from unittest import mock

from mitup_bot.models import User


def test_user_dont_exist(mock_session: mock.MagicMock):
    with User.open_session():
        with mock.patch("mitup_bot.models.users.select") as select_mock:
            mock_session.exec.return_value.first.return_value = None
            user = User.find_by_tg_user_id(1)

            select_mock.assert_called_once_with(User)

            assert user is None


def test_user_exist(mock_session: mock.MagicMock):
    with User.open_session():
        with mock.patch("mitup_bot.models.users.select") as select_mock:
            user = User.find_by_tg_user_id(1)

            select_mock.assert_called_once_with(User)
            mock_session.exec.assert_called_with(select_mock.return_value.where())

            assert user is not None


def test_settings_exist(mock_session: mock.MagicMock):
    with User.open_session():
        with mock.patch("mitup_bot.models.users.select") as select_mock:
            user = User.get_settings_from_user(1)

            select_mock.assert_called_once_with(User)
            mock_session.exec.assert_called_with(select_mock.return_value.where())

            assert user is not None
