import pytest

from mitup_bot.callback_data import BackOrigin, BackTarget
from mitup_bot.utils.messages import ButtonMessages
from mitup_bot.views.navigation import back_button_for, main_menu_back_button


@pytest.mark.parametrize(
    "origin, expected_label, expected_wire",
    [
        (None, ButtonMessages.MAIN_MENU, "show;main_menu:"),
        (BackOrigin(BackTarget.MEETING_EDITOR, 42), ButtonMessages.MEETING, "edit;meeting:42"),
    ],
    ids=["no_origin", "meeting_editor"],
)
def test_each_target_resolves_to_its_own_screen(
    lang: str, origin: BackOrigin | None, expected_label: ButtonMessages, expected_wire: str
):
    button = back_button_for(origin, lang)

    assert button.text == expected_label.back(lang=lang)
    assert str(button.callback_data) == expected_wire


def test_a_meeting_target_naming_no_meeting_falls_back_to_the_main_menu(lang: str):
    """Callback data is client-supplied, so an origin arriving without its id addresses no screen."""
    assert back_button_for(BackOrigin(BackTarget.MEETING_EDITOR), lang) == main_menu_back_button(lang)


@pytest.mark.parametrize("target", list(BackTarget), ids=lambda target: target.name.lower())
def test_every_target_resolves_to_a_button(lang: str, target: BackTarget):
    """A target added to the enum without a mapping here would leave a screen with no way back."""
    button = back_button_for(BackOrigin(target, 42), lang)

    assert button.text
    assert button.callback_data is not None
