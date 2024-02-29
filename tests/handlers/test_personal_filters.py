from unittest import mock

from mitup_bot.handlers import UserExistFilter


def test_user_exist_filter_without_effective_user(mock_session: mock.MagicMock):
    update = mock.MagicMock()

    update.effective_user = None
    assert UserExistFilter().filter(update) is False


def test_user_exist_filter_with_effective_user(mock_session: mock.MagicMock):
    update = mock.MagicMock()
    update.effective_user.id = 1

    with mock.patch("mitup_bot.models.User.find_by_tg_user_id") as mock_find_user:
        mock_find_user.return_value = mock.sentinel.user
        assert UserExistFilter().filter(update) is True
        mock_find_user.assert_called_once_with(mock_session, 1)


def test_user_exist_filter_with_effective_user_not_found(mock_session: mock.MagicMock):
    update = mock.MagicMock()
    update.effective_user.id = 1

    with mock.patch("mitup_bot.models.User.find_by_tg_user_id") as mock_find_user:
        mock_find_user.return_value = None
        assert UserExistFilter().filter(update) is False
        mock_find_user.assert_called_once_with(mock_session, 1)
