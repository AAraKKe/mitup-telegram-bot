import pytest

from mitup_bot.models import Meetup, User
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import ButtonConfig, MitupView
from tests.helpers import create_meetup, create_user

# --- Invite button visibility in main_view and external_view ---


def invite_buttons(view: MitupView, meeting: Meetup) -> list[ButtonConfig]:
    invite_cb = cb.INVITE.with_id(meeting.db_id)
    return [btn for row in view.keyboard for btn in row if btn.callback_data == invite_cb]


@pytest.mark.parametrize("allow_invitation", [True, False], ids=["allow_invitation", "no_invitation"])
def test_main_view_invite_button_visibility(allow_invitation: bool, user_with_settings: User):
    meeting = create_meetup(id=10, owner=user_with_settings, invitation=allow_invitation)

    view = meeting.main_view()
    found_invite_buttons = invite_buttons(view, meeting)

    if allow_invitation:
        assert len(found_invite_buttons) == 1  # INVITE button must appear exactly once
    else:
        assert len(found_invite_buttons) == 0  # INVITE button must be absent


@pytest.mark.parametrize("allow_invitation", [True, False], ids=["allow_invitation", "no_invitation"])
def test_external_view_invite_button_visibility(allow_invitation: bool, user_with_settings: User):
    meeting = create_meetup(id=11, owner=user_with_settings, invitation=allow_invitation)

    view = meeting.external_view()
    found_invite_buttons = invite_buttons(view, meeting)

    if allow_invitation:
        assert len(found_invite_buttons) == 1  # INVITE button must appear exactly once
    else:
        assert len(found_invite_buttons) == 0  # INVITE button must be absent


def test_participants_list_text_preserves_entities_from_participant_name():
    owner = create_user(id=1, first_name="Owner")
    meeting = create_meetup(id=1, owner=owner)
    invited = create_user(id=2, first_name="Bob")
    inviter = create_user(id=3, first_name="Alice")
    meeting.create_joined_link(invited, is_waiting_list=False, invited_by=inviter)

    result = meeting.participants_list_text
    # The text must include the participant name and the invited-by annotation.
    assert "Bob" in result.text
    assert "Alice" in result.text
    # Entities from the invited-by FormattedText must survive into the list.
    assert result.entities, "expected formatting entities from the invited-by text to be present"
