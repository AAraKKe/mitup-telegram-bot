import datetime as dt

import pytest

from mitup_bot import docs_links
from mitup_bot.callback_data import CallbackData
from mitup_bot.keyboards import ButtonConfig
from mitup_bot.utils import Emojis
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.entities import parse_format_tags
from mitup_bot.utils.messages import AdminMessages, ButtonMessages, HelpMessages, PrivacyMessages
from mitup_bot.views import MitupView, RenderContext, factory


def test_edit_meeting_property_view_without_extra_options(lang: str):
    message = "Test message"
    meeting_id = 1

    view = factory.edit_meeting_property_view(RenderContext(lang=lang), message=message, meeting_id=meeting_id)
    expected_view = MitupView(
        description=message,
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.EDIT.back(lang=lang), callback_data=cb.EDIT_MEETING.with_id(meeting_id)
                ),
            ],
        ],
    )

    assert expected_view == view


def test_edit_meeting_property_view_with_extra_buttons(lang: str):
    message = "Test message"
    meeting_id = 1
    extra_buttons = [
        [
            ButtonConfig(text="Option 1", callback_data="option_1"),
            ButtonConfig(text="Option 2", callback_data="option_2"),
        ],
        [
            ButtonConfig(text="Option 3", callback_data="option_3"),
        ],
    ]

    view = factory.edit_meeting_property_view(
        RenderContext(lang=lang), message=message, meeting_id=meeting_id, extra_buttons=extra_buttons
    )
    expected_view = MitupView(
        description=message,
        keyboard=[
            [
                ButtonConfig(text="Option 1", callback_data="option_1"),
                ButtonConfig(text="Option 2", callback_data="option_2"),
            ],
            [
                ButtonConfig(text="Option 3", callback_data="option_3"),
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.EDIT.back(lang=lang), callback_data=cb.EDIT_MEETING.with_id(meeting_id)
                ),
            ],
        ],
    )

    assert expected_view == view


def test_edit_meeting_property_view_with_custom_back_button(lang: str):
    message = "Test message"
    meeting_id = 1
    custom_back = ButtonConfig(text="Custom back", callback_data="custom_back")

    view = factory.edit_meeting_property_view(
        RenderContext(lang=lang), message=message, meeting_id=meeting_id, back_button=custom_back
    )
    expected_view = MitupView(
        description=message,
        keyboard=[
            [
                custom_back,
            ],
        ],
    )

    assert expected_view == view


@pytest.mark.parametrize(
    "back_callback,back_button_text",
    [
        (cb.REOPEN_START_EDITOR, ButtonMessages.DATE_TIME),
        (cb.REOPEN_END_EDITOR, ButtonMessages.END_DATE_TIME),
    ],
    ids=["start", "end"],
)
def test_edit_meeting_date_view_back_button_names_the_screen_it_returns_to(
    lang: str, back_callback: CallbackData, back_button_text: ButtonMessages
):
    """The back button's label must name the editor the callback actually reopens.

    The same calendar serves the start and the end of a meeting, and each returns to its own
    editor — a label naming anything else (the Edit menu, say) describes a screen the tap never
    reaches.
    """
    meeting_id = 7
    view = factory.edit_meeting_date_view(
        RenderContext(lang=lang),
        meeting_id=meeting_id,
        anchor_date=dt.date(2024, 11, 15),
        current_date=dt.date(2024, 11, 15),
        new=False,
        set_date_callback=cb.PICK_START_DATE,
        nav_callback=cb.NAVIGATE_START_CALENDAR,
        back_callback=back_callback,
        back_button_text=back_button_text,
    )

    back_button = view.keyboard[-1][0]
    assert back_button == ButtonConfig(
        text=back_button_text.back(lang=lang),
        callback_data=back_callback.with_id(meeting_id),
    )


def test_main_menu_view_hides_admin_button_by_default(lang: str):
    view = factory.main_menu_view(RenderContext(lang=lang))

    admin_button = ButtonConfig(text=AdminMessages.BUTTON_ADMIN.get_text(lang=lang), callback_data=cb.ADMIN_MENU)
    all_buttons = [button for row in view.keyboard for button in row]
    assert admin_button not in all_buttons


def test_main_menu_view_default_keyboard_matches_non_admin(lang: str):
    # The is_admin default must render exactly today's keyboard, unchanged.
    assert factory.main_menu_view(RenderContext(lang=lang)) == factory.main_menu_view(
        RenderContext(lang=lang, is_admin=False)
    )


def test_main_menu_view_appends_admin_button_for_admins(lang: str):
    view = factory.main_menu_view(RenderContext(lang=lang, is_admin=True))

    # The admin entry is a full-width row appended at the very bottom.
    assert view.keyboard[-1] == [
        ButtonConfig(text=AdminMessages.BUTTON_ADMIN.get_text(lang=lang), callback_data=cb.ADMIN_MENU)
    ]
    # Everything above the admin row is identical to the non-admin keyboard.
    assert view.keyboard[:-1] == factory.main_menu_view(RenderContext(lang=lang)).keyboard


def test_main_menu_view_help_button_opens_the_help_screen(lang: str):
    view = factory.main_menu_view(RenderContext(lang=lang))

    help_button = ButtonConfig(text=ButtonMessages.HELP.get_text(lang=lang), callback_data=cb.HELP)
    all_buttons = [button for row in view.keyboard for button in row]
    assert help_button in all_buttons


def test_help_view(lang: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(docs_links.DocsState, "base_url", "https://staging.mitup.social")

    view = factory.help_view(RenderContext(lang=lang))

    expected_view = MitupView(
        HelpMessages.DESCRIPTION.get(lang=lang),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.OPEN_USER_GUIDE.get_text(lang=lang),
                    url="https://staging.mitup.social/user-guide/",
                )
            ],
            [
                ButtonConfig(
                    text=ButtonMessages.JOIN_COMMUNITY_GROUP.get_text(lang=lang),
                    url="https://t.me/mitupgroup",
                )
            ],
            [ButtonConfig(text=ButtonMessages.MAIN_MENU.back(lang=lang), callback_data=cb.MAIN_MENU)],
        ],
    )

    assert expected_view == view


def test_help_view_links_to_the_community_group(lang: str):
    view = factory.help_view(RenderContext(lang=lang))

    community_button = ButtonConfig(
        text=ButtonMessages.JOIN_COMMUNITY_GROUP.get_text(lang=lang), url=factory.COMMUNITY_GROUP_URL
    )
    all_buttons = [button for row in view.keyboard for button in row]
    assert community_button in all_buttons


def test_help_view_message_contains_the_support_email(lang: str):
    view = factory.help_view(RenderContext(lang=lang))

    assert "support@mitup.social" in view.description.text


def test_settings_view_privacy_button_opens_the_privacy_screen(lang: str):
    view = factory.settings_view(RenderContext(lang=lang))

    privacy_button = ButtonConfig(text=ButtonMessages.PRIVACY.get_text(lang=lang), callback_data=cb.EDIT_PRIVACY)
    all_buttons = [button for row in view.keyboard for button in row]
    assert privacy_button in all_buttons


def test_privacy_view(lang: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(docs_links.DocsState, "base_url", "https://staging.mitup.social")

    view = factory.privacy_view(RenderContext(lang=lang))

    expected_view = MitupView(
        PrivacyMessages.DESCRIPTION.get(lang=lang),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.PRIVACY_POLICY.get_text(lang=lang),
                    url="https://staging.mitup.social/faq/privacy/",
                )
            ],
            [ButtonConfig(text=ButtonMessages.EXPORT_MY_DATA.get_text(lang=lang), callback_data=cb.EXPORT_USER_DATA)],
            [ButtonConfig(text=ButtonMessages.DELETE_MY_DATA.get_text(lang=lang), callback_data=cb.DELETE_USER_DATA)],
            [ButtonConfig(text=ButtonMessages.SETTINGS.back(lang=lang), callback_data=cb.SETTINGS)],
        ],
    )

    assert expected_view == view


def test_admin_menu_view(lang: str):
    view = factory.admin_menu_view(RenderContext(lang=lang))

    expected_view = MitupView(
        AdminMessages.MENU_DESCRIPTION.get(lang=lang),
        keyboard=[
            [
                ButtonConfig(text=AdminMessages.BUTTON_BROADCAST.get_text(lang=lang), callback_data=cb.BROADCAST),
                ButtonConfig(
                    text=AdminMessages.BUTTON_SUPPORTER_GRANTS.get_text(lang=lang),
                    callback_data=cb.SUPPORTER_GRANT,
                ),
            ],
            [
                ButtonConfig(text=ButtonMessages.MAIN_MENU.back(lang=lang), callback_data=cb.MAIN_MENU),
            ],
        ],
    )

    assert expected_view == view


def test_broadcast_recipient_keyboard(lang: str):
    keyboard = factory.broadcast_recipient_keyboard(lang)

    # A single row with a plain "Main Menu" button (no « back decoration) wired to SEND_MAIN_MENU.
    assert keyboard == [
        [ButtonConfig(text=ButtonMessages.MAIN_MENU.get_text(lang=lang), callback_data=cb.SEND_MAIN_MENU)]
    ]


def test_broadcast_recipient_view_pairs_the_rendered_body_with_the_recipient_keyboard(lang: str):
    view = factory.broadcast_recipient_view("<b>hi</b>", lang)

    assert view.description == parse_format_tags("<b>hi</b>", {})
    assert view.keyboard == factory.broadcast_recipient_keyboard(lang)


@pytest.mark.parametrize("option", [True, False])
def test_flag_button(option: bool):
    callback_data = cb.SET_DEFAULT_WAITING_LIST
    text = ButtonMessages.WAITING_LIST.get(lang="en")
    emoji = Emojis.CHECK if option else Emojis.RED_CIRCLE

    button = factory.options_button(callback_data=callback_data, text=text, option=option)
    expected_button = ButtonConfig(text=f"{emoji} {text}", callback_data=callback_data)

    assert expected_button == button
