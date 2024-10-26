import pytest
from telegram import Update

from mitup_bot.exceptions import EffectiveUserNotSet
from mitup_bot.models import Settings, build
from tests.helpers import UpdateRequest


@pytest.mark.parametrize("update", [UpdateRequest()], indirect=True)
def test_build_user(update: Update):
    user = build.user_from_update(update)

    assert update.effective_user is not None
    assert user.tg_user_id == update.effective_user.id
    assert user.first_name == update.effective_user.first_name
    assert user.last_name == update.effective_user.last_name
    assert user.username == update.effective_user.username
    assert user.settings == Settings()


@pytest.mark.parametrize("update", [UpdateRequest(user=False)], indirect=True)
def test_build_user_without_effective_user_raises(update: Update):
    with pytest.raises(EffectiveUserNotSet):
        build.user_from_update(update)
