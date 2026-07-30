import pytest
from telegram import Update

from mitup_bot.acquisition import SHARED_CARD_SOURCE
from mitup_bot.handlers.registration_process.utils import get_or_create_onboarding_user, promote_onboarded_user
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
    new_user, is_new_user = await get_or_create_onboarding_user(mock_session, update, None)

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

    result, is_new_user = await get_or_create_onboarding_user(mock_session, update, None)

    assert result is existing_user
    assert not is_new_user
    assert result.settings.language == "gl_ES"
    mock_session.assert_not_added()


@pytest.mark.parametrize("update", [UpdateRequest()], indirect=True)
async def test_brand_new_user_is_stamped_with_the_payload_that_brought_them(
    update: Update,
    mock_session: MockDbSession,
):
    new_user, _ = await get_or_create_onboarding_user(mock_session, update, "src_web")

    assert new_user.acquisition_source == "src_web"


@pytest.mark.parametrize("update", [UpdateRequest()], indirect=True)
async def test_returning_user_keeps_the_source_recorded_when_they_first_arrived(
    update: Update,
    mock_session: MockDbSession,
):
    """A row already names where its owner came from; a later `/start` is a return, not an arrival."""
    existing_user = create_member(id=5, tg_user_id=123, status=UserStatus.LEFT)
    existing_user.acquisition_source = SHARED_CARD_SOURCE
    mock_session.add_object(existing_user, "tg_user_id")

    result, _ = await get_or_create_onboarding_user(mock_session, update, "src_web")

    assert result.acquisition_source == SHARED_CARD_SOURCE


@pytest.mark.parametrize("update", [UpdateRequest()], indirect=True)
async def test_an_abandoned_onboarding_leaves_no_member_time(
    update: Update,
    mock_session: MockDbSession,
):
    """Creating the row is not becoming a member: someone who opens `/start` and never answers the
    timezone prompt must not count towards new members."""
    new_user, _ = await get_or_create_onboarding_user(mock_session, update, None)

    assert new_user.member_time is None


@pytest.mark.parametrize("update", [UpdateRequest()], indirect=True)
async def test_completing_onboarding_stamps_member_time(
    update: Update,
    mock_session: MockDbSession,
):
    """The promotion at the end of the funnel is the moment being recorded, and it has to record it
    for a brand-new user too — one who is already MEMBER by model default."""
    new_user, _ = await get_or_create_onboarding_user(mock_session, update, None)

    promote_onboarded_user(new_user)

    assert new_user.member_time is not None
