import pytest

from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Meetup, User
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import MitupView
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.meeting import shared_card
from tests.helpers import create_joined_link, create_meetup, create_user

# --- Invite button visibility in external_view ---


def invite_buttons(view: MitupView, meeting: Meetup) -> list[ButtonConfig]:
    invite_cb = cb.INVITE.with_id(meeting.db_id)
    return [btn for row in view.menu for btn in row if btn.callback_data == invite_cb]


@pytest.mark.parametrize("allow_invitation", [True, False], ids=["allow_invitation", "no_invitation"])
def test_external_view_invite_button_visibility(allow_invitation: bool, user_with_settings: User):
    meeting = create_meetup(id=11, owner=user_with_settings, invitation=allow_invitation)

    view = meeting_views.external_view(meeting)
    found_invite_buttons = invite_buttons(view, meeting)

    if allow_invitation:
        assert len(found_invite_buttons) == 1  # INVITE button must appear exactly once
    else:
        assert len(found_invite_buttons) == 0  # INVITE button must be absent


def test_the_attendee_list_keeps_the_formatting_of_an_invited_name():
    owner = create_user(id=1, first_name="Owner")
    meeting = create_meetup(id=1, owner=owner)
    invited = create_user(id=2, first_name="Bob")
    inviter = create_user(id=3, first_name="Alice")
    create_joined_link(invited, meeting, id=1, invited_by=inviter)

    names = shared_card.attendee_names(meeting, None)

    assert "Bob" in names.text
    assert "Alice" in names.text
    # The italic of the invited-by fragment survives the trip into rich content.
    assert "<i>" in names.html
