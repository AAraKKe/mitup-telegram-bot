import pytest
from telegram import Update

from mitup_bot.handlers.registration_process.utils import get_or_create_onboarding_user
from mitup_bot.models import User
from mitup_bot.models.users import UserStatus
from tests.helpers import UpdateRequest, create_member
from tests.helpers.stub_db import MockDbSession


@pytest.mark.parametrize(
    ("update", "expected_language"),
    [
        (UpdateRequest(language_code="es-ES"), "es_ES"),
        (UpdateRequest(language_code="pt-br"), "pt_BR"),
        (UpdateRequest(language_code="fr"), "en"),
        (UpdateRequest(language_code=None), "en"),
    ],
    indirect=["update"],
    ids=["spanish", "brazilian-portuguese", "unsupported", "absent"],
)
async def test_brand_new_user_settings_language_seeded_from_client_language(
    update: Update,
    mock_session: MockDbSession,
    expected_language: str,
):
    new_user, is_new_user = await get_or_create_onboarding_user(mock_session, update)

    assert is_new_user

    added_users = [obj for obj in mock_session.objects_added if isinstance(obj, User)]
    assert added_users == [new_user]
    assert new_user.settings.language == expected_language


@pytest.mark.parametrize("update", [UpdateRequest(language_code="es")], indirect=True)
async def test_existing_user_keeps_stored_language(
    update: Update,
    mock_session: MockDbSession,
):
    """A re-onboarding user's stored language must survive a differing client language_code."""
    existing_user = create_member(id=5, tg_user_id=123, language="gl_ES", status=UserStatus.LEFT)
    mock_session.add_object(existing_user, "tg_user_id")

    result, is_new_user = await get_or_create_onboarding_user(mock_session, update)

    assert result is existing_user
    assert not is_new_user
    assert result.settings.language == "gl_ES"
    mock_session.assert_not_added()
