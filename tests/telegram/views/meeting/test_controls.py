from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Meetup
from mitup_bot.translations import SUPPORTED_LANGUAGES
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages
from mitup_bot.views.meeting.controls import join_leave_row, main_menu_back_button, meeting_chip
from tests.telegram.views.meeting.helpers import owned_meeting


def test_meeting_chip_wires_a_catalog_label_to_the_meetings_own_callback(lang: str):
    meeting = owned_meeting(lang=lang)

    chip = meeting_chip(ButtonMessages.LEAVE, meeting, cb.LEAVE, style="danger")

    assert chip == ButtonConfig(
        text=ButtonMessages.LEAVE.text(lang=lang),
        callback_data=cb.LEAVE.with_id(meeting.db_id),
        style="danger",
    )


def test_meeting_chip_is_unstyled_unless_the_caller_asks_for_a_style(lang: str):
    meeting = owned_meeting(lang=lang)

    assert meeting_chip(ButtonMessages.JOIN, meeting, cb.JOIN).style is None


def test_meeting_chip_reads_in_the_readers_language_rather_than_the_meetings(lang: str):
    """A chip is a control the reader taps, so it speaks to them even on a meeting held in another
    language."""
    meeting = owned_meeting(lang=lang)
    meeting.language = next(code for code in SUPPORTED_LANGUAGES if code != meeting.user_language)

    chip = meeting_chip(ButtonMessages.LEAVE, meeting, cb.LEAVE)

    assert chip.text == ButtonMessages.LEAVE.text(lang=meeting.user_language)


def test_main_menu_back_button_sends_the_reader_to_the_main_menu(lang: str):
    meeting = owned_meeting(lang=lang)

    assert main_menu_back_button(meeting) == ButtonConfig(
        text=ButtonMessages.MAIN_MENU.back(lang=lang), callback_data=cb.MAIN_MENU
    )


def test_join_leave_row_is_the_way_in_beside_the_way_out(lang: str):
    meeting = owned_meeting(lang=lang, invitation=False)

    assert join_leave_row(meeting, lang) == [
        ButtonConfig(
            text=ButtonMessages.JOIN.text(lang=lang),
            callback_data=cb.JOIN.with_id(meeting.db_id),
            style="success",
        ),
        ButtonConfig(
            text=ButtonMessages.LEAVE.text(lang=lang),
            callback_data=cb.LEAVE.with_id(meeting.db_id),
            style="danger",
        ),
    ]


def test_join_leave_row_slots_the_invite_between_them_when_invitations_are_open(lang: str):
    meeting = owned_meeting(lang=lang, invitation=True)

    assert join_leave_row(meeting, lang) == [
        ButtonConfig(
            text=ButtonMessages.JOIN.text(lang=lang),
            callback_data=cb.JOIN.with_id(meeting.db_id),
            style="success",
        ),
        ButtonConfig(text=ButtonMessages.INVITE.text(lang=lang), callback_data=cb.INVITE.with_id(meeting.db_id)),
        ButtonConfig(
            text=ButtonMessages.LEAVE.text(lang=lang),
            callback_data=cb.LEAVE.with_id(meeting.db_id),
            style="danger",
        ),
    ]


def test_join_leave_row_accents_the_way_in_and_the_way_out(lang: str):
    """The colours say which way each button moves the reader; the labels carry their emoji so the
    meaning survives a context that repaints them."""
    join, leave = join_leave_row(owned_meeting(lang=lang), lang)

    assert (join.style, leave.style) == ("success", "danger")


def full_meeting(*, waiting_list: bool, lang: str) -> Meetup:
    meeting = owned_meeting(lang=lang, guests=2)
    meeting.max_members = 2
    meeting.waiting_list = waiting_list
    return meeting


def test_join_leave_row_reports_a_full_meeting_with_an_inert_chip(lang: str):
    meeting = full_meeting(waiting_list=False, lang=lang)

    join, _ = join_leave_row(meeting, lang, inert_when_full=True)

    assert join == ButtonConfig(text=ButtonMessages.FULL.text(lang=lang), disabled=True)


def test_join_leave_row_keeps_the_way_in_while_a_waiting_list_takes_people(lang: str):
    """A full meeting with a waiting list is still joinable: the join lands on the list."""
    meeting = full_meeting(waiting_list=True, lang=lang)

    join, _ = join_leave_row(meeting, lang, inert_when_full=True)

    assert join.callback_data == cb.JOIN.with_id(meeting.db_id)


def test_join_leave_row_keeps_a_tappable_join_on_a_full_meeting_unless_asked_otherwise(lang: str):
    """A classic keyboard refuses a disabled button outright, so a surface serialized that way asks
    for the tappable Join and lets the handler answer with the alert saying the meeting is full."""
    meeting = full_meeting(waiting_list=False, lang=lang)

    join, _ = join_leave_row(meeting, lang)

    assert join.callback_data == cb.JOIN.with_id(meeting.db_id)
    assert not join.disabled
