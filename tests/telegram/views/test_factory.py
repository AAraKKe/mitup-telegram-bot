import pytest

from mitup_bot import docs_links
from mitup_bot.callback_data import CallbackData
from mitup_bot.keyboards import ButtonConfig, ButtonStyle
from mitup_bot.models import MeetingCounts, User
from mitup_bot.translations import SUPPORTED_LANGUAGES
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import (
    AdminMessages,
    ButtonMessages,
    HelpMessages,
    MainMenuMessages,
    MeetingCreationMessages,
    PrivacyMessages,
    SettingsMessages,
)
from mitup_bot.utils.rich_message import RichContent, RichDocument, RichTag, button_content, button_row_content
from mitup_bot.utils.rich_template import render_rich_tags
from mitup_bot.views import MitupView, RenderContext, factory
from mitup_bot.views.datetime_format import duration_minutes_content

STAGING_DOCS_URL = "https://staging.mitup.social"


def test_main_menu_view_opens_on_the_welcome_heading_and_tagline(lang: str):
    view = factory.main_menu_view(RenderContext(lang=lang))

    heading = MainMenuMessages.WELCOME.rich(lang=lang).wrap(RichTag.H1).html
    assert view.message.html.startswith(heading + MainMenuMessages.TAGLINE.rich(lang=lang).html)


def test_main_menu_view_replaces_the_opening_alone(lang: str):
    view = factory.main_menu_view(RenderContext(lang=lang), message=RichContent("your meeting is gone"))

    assert view.message.text.startswith("your meeting is gone")
    assert MainMenuMessages.WELCOME.text(lang=lang) not in view.message.text
    assert MainMenuMessages.TAGLINE.text(lang=lang) not in view.message.text
    assert MainMenuMessages.MEETINGS_SECTION.text(lang=lang) in view.message.text


def test_main_menu_view_makes_creating_a_meeting_the_call_to_action_above_the_sections(lang: str):
    view = factory.main_menu_view(RenderContext(lang=lang))

    new_meeting = ButtonConfig(
        text=ButtonMessages.NEW_MEETING.text(lang=lang), callback_data=cb.CREATE_MEETING, style="primary"
    )
    html = view.message.html
    assert html.index(button_content(new_meeting).html) < html.index("<hr/>")


def test_main_menu_view_pairs_active_and_joined_over_a_full_width_past_row(lang: str):
    view = factory.main_menu_view(RenderContext(lang=lang))

    pair = [
        ButtonConfig(
            text=ButtonMessages.ACTIVE_MEETINGS_CHIP.text(lang=lang),
            callback_data=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(1),
        ),
        ButtonConfig(
            text=ButtonMessages.JOINED_MEETINGS_CHIP.text(lang=lang),
            callback_data=cb.SHOW_JOINED_MEETINGS_PAGE.with_id(1),
        ),
    ]
    past = [ButtonConfig(text=ButtonMessages.PAST_MEETINGS_CHIP.text(lang=lang), callback_data=cb.PAST_MEETINGS)]
    assert button_row_content(pair).html + button_row_content(past).html in view.message.html


MENU_LIST_CHIPS = [
    (ButtonMessages.ACTIVE_MEETINGS_CHIP, cb.SHOW_ACTIVE_MEETING_PAGE.with_id(1), 2),
    (ButtonMessages.JOINED_MEETINGS_CHIP, cb.SHOW_JOINED_MEETINGS_PAGE.with_id(1), 1),
    (ButtonMessages.PAST_MEETINGS_CHIP, cb.PAST_MEETINGS, 3),
]
"""Each main-menu list chip with the callback it carries and its size in `SIZED_COUNTS`."""

SIZED_COUNTS = MeetingCounts(active=2, joined=1, past=3)


@pytest.mark.parametrize(("label", "callback_data", "count"), MENU_LIST_CHIPS, ids=["active", "joined", "past"])
def test_main_menu_view_numbers_each_list_chip_with_the_size_of_its_list(
    lang: str, label: ButtonMessages, callback_data: CallbackData, count: int
):
    view = factory.main_menu_view(RenderContext(lang=lang), counts=SIZED_COUNTS)

    chip = ButtonConfig(
        text=ButtonMessages.COUNTED_CHIP.text(lang=lang, label=label.text(lang=lang), count=count),
        callback_data=callback_data,
    )
    assert button_content(chip).html in view.message.html


@pytest.mark.parametrize(("label", "callback_data", "_unused"), MENU_LIST_CHIPS, ids=["active", "joined", "past"])
def test_main_menu_view_makes_a_chip_inert_when_its_list_is_empty(
    lang: str, label: ButtonMessages, callback_data: CallbackData, _unused: int
):
    view = factory.main_menu_view(RenderContext(lang=lang), counts=MeetingCounts(active=0, joined=0, past=0))

    inert = ButtonConfig(
        text=ButtonMessages.COUNTED_CHIP.text(lang=lang, label=label.text(lang=lang), count=0), disabled=True
    )
    assert button_content(inert).html in view.message.html
    assert str(callback_data) not in view.message.html


@pytest.mark.parametrize(("label", "callback_data", "_unused"), MENU_LIST_CHIPS, ids=["active", "joined", "past"])
def test_main_menu_view_leaves_the_list_chips_unnumbered_without_counts(
    lang: str, label: ButtonMessages, callback_data: CallbackData, _unused: int
):
    view = factory.main_menu_view(RenderContext(lang=lang))

    plain = ButtonConfig(text=label.text(lang=lang), callback_data=callback_data)
    assert button_content(plain).html in view.message.html
    assert ButtonMessages.COUNTED_CHIP.text(lang=lang, label=label.text(lang=lang), count=0) not in view.message.html


def test_main_menu_view_puts_the_account_actions_under_their_own_title(lang: str):
    view = factory.main_menu_view(RenderContext(lang=lang))

    account_title = MainMenuMessages.ACCOUNT_SECTION.rich(lang=lang).wrap(RichTag.BOLD).html
    pair = [
        ButtonConfig(text=ButtonMessages.SETTINGS.text(lang=lang), callback_data=cb.SETTINGS),
        ButtonConfig(text=ButtonMessages.HELP.text(lang=lang), callback_data=cb.HELP),
    ]
    collaborate = [ButtonConfig(text=ButtonMessages.COLLABORATE.text(lang=lang), callback_data=cb.COLLABORATE)]
    rows = button_row_content(pair).html + button_row_content(collaborate).html
    assert view.message.html.index(account_title) < view.message.html.index(rows)


def test_main_menu_view_help_chip_opens_the_help_screen(lang: str):
    view = factory.main_menu_view(RenderContext(lang=lang))

    help_chip = ButtonConfig(text=ButtonMessages.HELP.text(lang=lang), callback_data=cb.HELP)
    assert button_content(help_chip).html in view.message.html


def test_main_menu_view_hides_admin_button_by_default(lang: str):
    view = factory.main_menu_view(RenderContext(lang=lang))

    assert view.menu == []


def test_main_menu_view_default_keyboard_matches_non_admin(lang: str):
    assert factory.main_menu_view(RenderContext(lang=lang)) == factory.main_menu_view(
        RenderContext(lang=lang, is_admin=False)
    )


def test_main_menu_view_appends_admin_button_for_admins(lang: str):
    view = factory.main_menu_view(RenderContext(lang=lang, is_admin=True))

    assert view.menu == [[ButtonConfig(text=AdminMessages.BUTTON_ADMIN.text(lang=lang), callback_data=cb.ADMIN_MENU)]]
    assert view.message == factory.main_menu_view(RenderContext(lang=lang)).message


def test_help_view_heads_the_screen_and_says_what_it_is_for(lang: str):
    view = factory.help_view(RenderContext(lang=lang))

    assert view.message.html.startswith(ButtonMessages.HELP.rich(lang=lang).wrap(RichTag.H2).html)
    assert HelpMessages.INTRO.text(lang=lang) in view.message.text


@pytest.mark.parametrize(
    "sentence, placeholder, label, url",
    [
        (HelpMessages.USER_GUIDE, "button_guide", ButtonMessages.USER_GUIDE, f"{STAGING_DOCS_URL}/user-guide/"),
        (HelpMessages.COMMUNITY_GROUP, "button_group", ButtonMessages.COMMUNITY_GROUP, factory.COMMUNITY_GROUP_URL),
        (HelpMessages.NEWS_CHANNEL, "button_channel", ButtonMessages.NEWS_CHANNEL, factory.NEWS_CHANNEL_URL),
    ],
    ids=["user_guide", "community_group", "news_channel"],
)
def test_help_view_opens_each_channel_from_the_chip_inside_its_sentence(
    lang: str,
    monkeypatch: pytest.MonkeyPatch,
    sentence: HelpMessages,
    placeholder: str,
    label: ButtonMessages,
    url: str,
):
    monkeypatch.setattr(docs_links.DocsState, "base_url", STAGING_DOCS_URL)

    view = factory.help_view(RenderContext(lang=lang))

    chip = ButtonConfig(text=label.text(lang=lang), url=url)
    assert sentence.rich(lang=lang, **{placeholder: chip}).html in view.message.html
    assert button_content(chip).html in view.message.html


def test_help_view_writes_the_support_email_bare(lang: str):
    view = factory.help_view(RenderContext(lang=lang))

    assert "support@mitup.social" in view.message.text
    assert "mailto:" not in view.message.html


def test_help_view_closes_on_the_way_back_to_the_main_menu(lang: str):
    view = factory.help_view(RenderContext(lang=lang))

    assert view.menu == [[ButtonConfig(text=ButtonMessages.MAIN_MENU.back(lang=lang), callback_data=cb.MAIN_MENU)]]


def settings_card_html(user: User, lang: str) -> str:
    return factory.settings_view(RenderContext(lang=lang), user).message.html


def test_settings_card_heads_the_screen_with_its_title(user_with_settings: User, lang: str):
    html = settings_card_html(user_with_settings, lang)

    assert html.startswith(ButtonMessages.SETTINGS.rich(lang=lang).wrap(RichTag.H2).html)


def test_settings_card_titles_every_section(user_with_settings: User, lang: str):
    html = settings_card_html(user_with_settings, lang)

    titles = [
        ButtonMessages.LANGUAGE,
        ButtonMessages.TIMEZONE,
        ButtonMessages.NOTIFICATIONS,
        ButtonMessages.TIMEOUT,
        ButtonMessages.DEFAULT_OPTIONS,
        ButtonMessages.PRIVACY,
    ]
    for title in titles:
        assert title.rich(lang=lang).wrap(RichTag.BOLD).html in html


def test_settings_card_offers_every_language_with_the_current_one_accented(user_with_settings: User, lang: str):
    user_with_settings.settings.language = lang

    html = settings_card_html(user_with_settings, lang)

    assert all(factory.LANGUAGE_BUTTONS[code].text(lang=lang) in html for code in SUPPORTED_LANGUAGES)
    assert all(str(cb.SET_LANGUAGE.with_id(index)) in html for index in range(len(SUPPORTED_LANGUAGES)))
    picked = str(cb.SET_LANGUAGE.with_id(SUPPORTED_LANGUAGES.index(lang)))
    assert f'data="{picked}" style="primary"' in html
    assert html.count('style="primary"') == 1


def test_settings_card_names_the_city_of_the_stored_timezone(user_with_settings: User, lang: str):
    user_with_settings.settings.timezone = "America/Argentina/Buenos_Aires"

    text = factory.settings_view(RenderContext(lang=lang), user_with_settings).message.text

    assert "Buenos Aires" in text
    assert "America/Argentina" not in text


def test_settings_card_names_utc_as_it_is_stored(user_with_settings: User, lang: str):
    user_with_settings.settings.timezone = "UTC"

    assert "UTC" in factory.settings_view(RenderContext(lang=lang), user_with_settings).message.text


@pytest.mark.parametrize(
    "notifications_enabled, label, style",
    [(True, ButtonMessages.ENABLED, "success"), (False, ButtonMessages.DISABLED, "danger")],
    ids=["enabled", "disabled"],
)
def test_settings_card_carries_the_notification_toggle_in_its_current_state(
    user_with_settings: User, lang: str, notifications_enabled: bool, label: ButtonMessages, style: ButtonStyle
):
    user_with_settings.settings.notification = notifications_enabled

    html = settings_card_html(user_with_settings, lang)

    chip = factory.toggle_chip(cb.TOGGLE_NOTIFICATIONS, notifications_enabled, lang)
    assert button_content(chip).html in html
    assert label.text(lang=lang) in html
    assert f'style="{style}"' in html


def test_settings_card_states_the_notification_lead_and_the_timeout(user_with_settings: User, lang: str):
    user_with_settings.settings.notification_time = 15
    user_with_settings.settings.timeout = 240

    html = settings_card_html(user_with_settings, lang)

    change = ButtonConfig(text=ButtonMessages.CHANGE.text(lang=lang), callback_data=cb.SET_NOTIFICATION_TIME)
    lead = SettingsMessages.NOTIFICATION_LEAD.rich(
        lang=lang, duration=duration_minutes_content(15, lang=lang), button_change=change
    )
    assert lead.html in html
    assert (
        SettingsMessages.TIMEOUT_VALUE.rich(lang=lang, duration=duration_minutes_content(240, lang=lang)).html in html
    )


@pytest.mark.parametrize(
    "label, callback_data",
    [
        (ButtonMessages.CHANGE, cb.EDIT_TIEMZONE),
        (ButtonMessages.CHANGE, cb.SET_NOTIFICATION_TIME),
        (ButtonMessages.CHANGE, cb.EDIT_TIMEOUT),
        (ButtonMessages.OPEN, cb.EDIT_DEFAULT_OPTIONS),
        (ButtonMessages.OPEN, cb.EDIT_PRIVACY),
    ],
    ids=["timezone", "notification_time", "timeout", "default_options", "privacy"],
)
def test_settings_card_carries_the_chip_that_opens_each_setting(
    user_with_settings: User, lang: str, label: ButtonMessages, callback_data: CallbackData
):
    chip = ButtonConfig(text=label.text(lang=lang), callback_data=callback_data)

    assert button_content(chip).html in settings_card_html(user_with_settings, lang)


def test_settings_card_closes_on_the_way_back_to_the_main_menu(user_with_settings: User, lang: str):
    view = factory.settings_view(RenderContext(lang=lang), user_with_settings)

    assert view.menu == [[ButtonConfig(text=ButtonMessages.MAIN_MENU.back(lang=lang), callback_data=cb.MAIN_MENU)]]


def test_privacy_view_heads_the_screen_and_states_who_owns_the_data(lang: str):
    view = factory.privacy_view(RenderContext(lang=lang))

    assert view.message.html.startswith(ButtonMessages.PRIVACY.rich(lang=lang).wrap(RichTag.H2).html)
    assert PrivacyMessages.INTRO.text(lang=lang) in view.message.text


@pytest.mark.parametrize(
    "title, explanation",
    [
        (PrivacyMessages.POLICY_TITLE, PrivacyMessages.POLICY),
        (PrivacyMessages.EXPORT_TITLE, PrivacyMessages.EXPORT),
        (PrivacyMessages.DELETE_TITLE, PrivacyMessages.DELETE),
    ],
    ids=["policy", "export", "deletion"],
)
def test_privacy_view_titles_every_section_and_explains_it(
    lang: str, title: PrivacyMessages, explanation: PrivacyMessages
):
    view = factory.privacy_view(RenderContext(lang=lang))

    assert title.rich(lang=lang).wrap(RichTag.BOLD).html in view.message.html
    assert explanation.text(lang=lang) in view.message.text


def test_privacy_view_puts_the_policy_link_on_its_title_line(lang: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(docs_links.DocsState, "base_url", STAGING_DOCS_URL)

    view = factory.privacy_view(RenderContext(lang=lang))

    title = PrivacyMessages.POLICY_TITLE.rich(lang=lang).wrap(RichTag.BOLD).html
    chip = button_content(
        ButtonConfig(text=ButtonMessages.READ.text(lang=lang), url=f"{STAGING_DOCS_URL}/faq/privacy/")
    )
    assert f"{title} {chip.html}" in view.message.html


def test_privacy_view_puts_the_export_chip_on_its_title_line(lang: str):
    view = factory.privacy_view(RenderContext(lang=lang))

    title = PrivacyMessages.EXPORT_TITLE.rich(lang=lang).wrap(RichTag.BOLD).html
    chip = button_content(ButtonConfig(text=ButtonMessages.EXPORT.text(lang=lang), callback_data=cb.EXPORT_USER_DATA))
    assert f"{title} {chip.html}" in view.message.html


def test_privacy_view_marks_deletion_as_the_destructive_one(lang: str):
    view = factory.privacy_view(RenderContext(lang=lang))

    chip = button_content(
        ButtonConfig(text=ButtonMessages.DELETE.text(lang=lang), callback_data=cb.DELETE_USER_DATA, style="danger")
    )
    assert chip.html in view.message.html


def test_privacy_view_carries_no_file_until_one_is_exported(lang: str):
    view = factory.privacy_view(RenderContext(lang=lang))

    assert view.document is None
    assert PrivacyMessages.EXPORT_ATTACHED.text(lang=lang) not in view.message.text


def test_privacy_view_attaches_the_export_and_says_it_came_with_the_screen(lang: str):
    export = RichDocument(content=b"{}", filename="export.json")

    view = factory.privacy_view(RenderContext(lang=lang), export=export)

    assert view.document is export
    attached = PrivacyMessages.EXPORT_ATTACHED.rich(lang=lang).html
    assert f"{PrivacyMessages.EXPORT.rich(lang=lang).html}<br/>{attached}" in view.message.html


def test_privacy_view_closes_on_the_way_back_to_settings(lang: str):
    view = factory.privacy_view(RenderContext(lang=lang))

    assert view.menu == [[ButtonConfig(text=ButtonMessages.SETTINGS.back(lang=lang), callback_data=cb.SETTINGS)]]


def test_admin_menu_view(lang: str):
    view = factory.admin_menu_view(RenderContext(lang=lang))

    expected_view = MitupView(
        AdminMessages.MENU_DESCRIPTION.rich(lang=lang),
        menu=[
            [
                ButtonConfig(text=AdminMessages.BUTTON_BROADCAST.text(lang=lang), callback_data=cb.BROADCAST),
                ButtonConfig(
                    text=AdminMessages.BUTTON_SUPPORTER_GRANTS.text(lang=lang),
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
    assert keyboard == [[ButtonConfig(text=ButtonMessages.MAIN_MENU.text(lang=lang), callback_data=cb.SEND_MAIN_MENU)]]


def test_broadcast_recipient_view_pairs_the_rendered_body_with_the_recipient_keyboard(lang: str):
    view = factory.broadcast_recipient_view("<b>hi</b>", lang)

    assert view.message == render_rich_tags("<b>hi</b>", field="test")
    assert view.menu == factory.broadcast_recipient_keyboard(lang)


def test_language_grid_accents_the_current_language_alone(lang: str):
    html = factory.language_grid_content(lang, current="es_ES", callback_data=cb.SET_LANGUAGE).html

    for index, lang_code in enumerate(SUPPORTED_LANGUAGES):
        label = factory.LANGUAGE_BUTTONS[lang_code].text(lang=lang)
        assert label in html
        accented = f'data="{cb.SET_LANGUAGE.with_id(index)}" style="primary"' in html
        assert accented == (lang_code == "es_ES")


@pytest.mark.parametrize(
    "option, label, style",
    [(True, ButtonMessages.ENABLED, "success"), (False, ButtonMessages.DISABLED, "danger")],
    ids=["enabled", "disabled"],
)
def test_toggle_chip_shows_current_state(lang: str, option: bool, label: ButtonMessages, style: ButtonStyle):
    chip = factory.toggle_chip(cb.SET_MEETING_PUBLIC.with_id(5), option, lang)

    assert chip == ButtonConfig(text=label.text(lang=lang), callback_data=cb.SET_MEETING_PUBLIC.with_id(5), style=style)


def test_conversation_interrupted_view_is_a_nudge_with_its_cancel_chip_inline(lang: str):
    view = factory.conversation_interrupted_view(
        RenderContext(lang=lang), notice=MeetingCreationMessages.ON_EXIT, cancel_callback=cb.CANCEL_CREATE_MEETING
    )

    cancel = ButtonConfig(text=ButtonMessages.CANCEL.text(lang=lang), callback_data=cb.CANCEL_CREATE_MEETING)
    assert view.message == MeetingCreationMessages.ON_EXIT.rich(lang=lang, button_cancel=cancel)
    assert button_content(cancel).html in view.message.html
    assert view.menu == []
