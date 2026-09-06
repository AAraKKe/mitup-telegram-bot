import pytest
from telegram import Update

from mitup_bot.keyboards import ButtonConfig
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import meeting as meeting_views
from tests.helpers import UpdateRequest, create_user
from tests.telegram.views.meeting.helpers import owned_meeting

CHAT_INSTANCE = "someinstance"


def test_view_for_gives_the_owner_the_owner_view_and_everyone_else_the_external_one():
    meeting = owned_meeting()
    stranger = create_user(id=99, tg_user_id=99, first_name="Stranger")

    assert meeting_views.view_for(meeting, meeting.owner) == meeting_views.owner_view(meeting)
    assert meeting_views.view_for(meeting, stranger) == meeting_views.external_view(meeting)


def test_view_for_hands_the_back_button_on_to_whichever_card_it_picks():
    meeting = owned_meeting()
    stranger = create_user(id=99, tg_user_id=99, first_name="Stranger")
    back = ButtonConfig(text="≪ Active meetings", callback_data=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(2))

    assert meeting_views.view_for(meeting, meeting.owner, back).menu[-1] == [back]
    assert meeting_views.view_for(meeting, stranger, back).menu[-1] == [back]


def test_keyboard_stored_for_an_owners_bot_chat_message_is_the_owner_menu(update: Update):
    meeting = owned_meeting()

    stored = meeting_views.keyboard_for_update(update, meeting, meeting.owner)

    assert stored == meeting_views.owner_view(meeting).menu


def test_keyboard_stored_for_a_guests_bot_chat_message_is_the_participant_menu(update: Update):
    meeting = owned_meeting()
    stranger = create_user(id=99, tg_user_id=99, first_name="Stranger")

    stored = meeting_views.keyboard_for_update(update, meeting, stranger)

    assert stored == meeting_views.external_view(meeting).menu


def test_a_caller_with_no_account_owns_nothing_and_gets_the_participant_menu(update: Update):
    meeting = owned_meeting()

    stored = meeting_views.keyboard_for_update(update, meeting, None)

    assert stored == meeting_views.external_view(meeting).menu


@pytest.mark.parametrize(
    "update", [UpdateRequest(callback_query=cb.JOIN.with_id(7), from_bot_chat=False)], indirect=True
)
def test_keyboard_stored_for_a_shared_message_is_rebuilt_with_the_chat_it_sits_in(update: Update):
    """The chat_instance is only known once the card is tapped, and it is what tells the keyboard
    the card is already searchable there."""
    meeting = owned_meeting()

    stored = meeting_views.keyboard_for_update(update, meeting, meeting.owner)

    assert stored == meeting_views.inline_view(meeting, chat_instance=CHAT_INSTANCE).menu


@pytest.mark.parametrize("update", [UpdateRequest(user=False, chat=False, message=False)], indirect=True)
def test_an_update_pointing_at_no_message_falls_back_to_the_shared_keyboard(update: Update):
    meeting = owned_meeting()

    stored = meeting_views.keyboard_for_update(update, meeting, meeting.owner)

    assert stored == meeting_views.inline_view(meeting).menu
