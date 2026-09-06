from collections.abc import Callable
from dataclasses import replace

import pytest

from mitup_bot.datetimes import DateFormat
from mitup_bot.emojis import Emojis
from mitup_bot.models import Settings, User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingEditSettingsMessages, SettingsMessages
from mitup_bot.utils.rich_message import button_content
from mitup_bot.views import MitupView
from mitup_bot.views.datetime_format import localized_datetime
from mitup_bot.views.factory import toggle_chip
from mitup_bot.views.meeting_settings import (
    default_behavior_view,
    default_meeting_settings_view,
    default_time_format_view,
)
from mitup_bot.views.settings_sections import SAMPLE_MOMENT


def card_html(settings: Settings) -> str:
    return default_meeting_settings_view(settings).message.html


def behavior_html(settings: Settings) -> str:
    return default_behavior_view(settings).message.html


def time_format_html(settings: Settings) -> str:
    return default_time_format_view(settings).message.html


def test_the_card_opens_on_its_title_and_the_line_saying_what_it_is(user_with_settings: User, lang: str):
    settings = user_with_settings.settings
    settings.language = lang

    html = card_html(settings)

    assert html.startswith(f"<h2>{ButtonMessages.DEFAULT_OPTIONS.text(lang=lang)}</h2>")
    assert SettingsMessages.DEFAULT_OPTIONS_LEAD.rich(lang=lang).html in html


def test_the_card_offers_the_two_sub_cards_and_no_language(user_with_settings: User, lang: str):
    """There is no default language: a meeting speaks its owner's until it is told otherwise."""
    settings = user_with_settings.settings
    settings.language = lang

    html = card_html(settings)
    behavior = MeetingEditSettingsMessages.BEHAVIOR_GROUP.rich(lang=lang).text
    time_format = MeetingEditSettingsMessages.TIME_FORMAT_GROUP.rich(lang=lang).text

    assert f"{Emojis.CONTROLS} <b>{behavior}</b>" in html
    assert f"{Emojis.CLOCK} <b>{time_format}</b>" in html
    assert MeetingEditSettingsMessages.BEHAVIOR_LINE.rich(lang=lang).html in html
    assert MeetingEditSettingsMessages.TIME_FORMAT_LINE.rich(lang=lang).html in html
    assert str(cb.OPEN_DEFAULT_BEHAVIOR) in html
    assert str(cb.OPEN_DEFAULT_TIME_FORMAT) in html
    assert str(cb.SET_LANGUAGE.with_id(0)) not in html


def test_the_card_holds_no_toggle_of_its_own(user_with_settings: User):
    """Every default lives on a sub-card, so the card itself flips nothing."""
    toggles = [
        cb.SET_DEFAULT_WAITING_LIST,
        cb.SET_DEFAULT_PUBLIC,
        cb.SET_DEFAULT_INVITATIONS,
        cb.SET_DEFAULT_INCOGNITO,
        cb.SET_DEFAULT_LOCK_ON_START,
        cb.SET_DEFAULT_SHOW_TIMEZONE,
        cb.SET_DEFAULT_CLOCK_24H,
    ]

    html = card_html(user_with_settings.settings)

    assert not any(str(toggle) in html for toggle in toggles)


def test_the_card_closes_on_the_way_back_to_settings(user_with_settings: User, lang: str):
    settings = user_with_settings.settings
    settings.language = lang

    view = default_meeting_settings_view(settings)

    assert view.menu[-1][0].callback_data == cb.SETTINGS
    assert view.menu[-1][0].text == ButtonMessages.SETTINGS.back(lang=lang)


def test_the_behavior_sub_card_titles_itself_and_says_the_states_are_tappable(user_with_settings: User, lang: str):
    settings = user_with_settings.settings
    settings.language = lang

    html = behavior_html(settings)
    title = MeetingEditSettingsMessages.BEHAVIOR_GROUP.rich(lang=lang).text

    assert html.startswith(f"<h2>{Emojis.CONTROLS} {title}</h2>")
    assert SettingsMessages.TOGGLE_HINT.rich(lang=lang).html in html


def test_the_behavior_sub_card_carries_every_behaviour_default(user_with_settings: User):
    toggles = [
        cb.SET_DEFAULT_WAITING_LIST,
        cb.SET_DEFAULT_PUBLIC,
        cb.SET_DEFAULT_INVITATIONS,
        cb.SET_DEFAULT_INCOGNITO,
        cb.SET_DEFAULT_LOCK_ON_START,
    ]

    html = behavior_html(user_with_settings.settings)

    assert all(str(toggle) in html for toggle in toggles)


def test_the_time_format_sub_card_titles_itself_and_says_the_states_are_tappable(user_with_settings: User, lang: str):
    settings = user_with_settings.settings
    settings.language = lang

    html = time_format_html(settings)
    title = MeetingEditSettingsMessages.TIME_FORMAT_GROUP.rich(lang=lang).text

    assert html.startswith(f"<h2>{Emojis.CLOCK} {title}</h2>")
    assert SettingsMessages.TOGGLE_HINT.rich(lang=lang).html in html


@pytest.mark.parametrize(
    "sub_card",
    [default_behavior_view, default_time_format_view],
    ids=["behavior", "time_format"],
)
def test_each_sub_card_closes_on_the_way_back_to_the_default_options_card(
    user_with_settings: User, sub_card: Callable[[Settings], MitupView], lang: str
):
    settings = user_with_settings.settings
    settings.language = lang

    back = sub_card(settings).menu[-1][0]

    assert back.callback_data == cb.EDIT_DEFAULT_OPTIONS
    assert back.text == ButtonMessages.DEFAULT_OPTIONS.back(lang=lang)


@pytest.mark.parametrize("value", [True, False], ids=["enabled", "disabled"])
def test_a_toggle_chip_reads_the_state_the_setting_is_in(user_with_settings: User, value: bool, lang: str):
    settings = user_with_settings.settings
    settings.language = lang
    settings.default_show_timezone = value
    settings.default_clock_24h = not value

    html = time_format_html(settings)

    assert button_content(toggle_chip(cb.SET_DEFAULT_SHOW_TIMEZONE, value, lang)).html in html
    assert button_content(toggle_chip(cb.SET_DEFAULT_CLOCK_24H, not value, lang)).html in html


def test_every_setting_states_what_it_does(user_with_settings: User, lang: str):
    """The defaults sub-cards explain their settings with the same sentences the meeting ones use."""
    settings = user_with_settings.settings
    settings.language = lang

    html = behavior_html(settings) + time_format_html(settings)
    explanations = [
        MeetingEditSettingsMessages.WAITING_LIST_EXPLANATION,
        MeetingEditSettingsMessages.PUBLIC_EXPLANATION,
        MeetingEditSettingsMessages.OPEN_INVITATIONS_EXPLANATION,
        MeetingEditSettingsMessages.INCOGNITO_EXPLANATION,
        MeetingEditSettingsMessages.LOCK_ON_START_EXPLANATION,
        MeetingEditSettingsMessages.SHOW_TIMEZONE_EXPLANATION,
        MeetingEditSettingsMessages.CLOCK_24H_EXPLANATION,
        MeetingEditSettingsMessages.DATE_FORMAT_EXPLANATION,
    ]

    assert all(explanation.rich(lang=lang).html in html for explanation in explanations)


@pytest.mark.parametrize("date_format", list(DateFormat))
def test_the_date_format_offers_every_format_with_the_default_accented(
    user_with_settings: User, date_format: DateFormat
):
    settings = user_with_settings.settings
    settings.default_date_format = date_format
    picked = str(cb.SET_DEFAULT_DATE_FORMAT.with_id(list(DateFormat).index(date_format)))

    html = time_format_html(settings)

    assert all(str(cb.SET_DEFAULT_DATE_FORMAT.with_id(index)) in html for index in range(len(DateFormat)))
    assert f'data="{picked}" style="primary"' in html
    assert html.count('style="primary"') == 1


def test_every_date_format_previews_the_sample_moment_in_the_users_own_timezone(user_with_settings: User):
    settings = user_with_settings.settings
    settings.default_clock_24h = False

    html = time_format_html(settings)

    expected = [
        localized_datetime(
            SAMPLE_MOMENT,
            lang=settings.language,
            tz=settings.tz,
            created=None,
            time_format=replace(settings.default_time_format, date_format=date_format),
        )
        for date_format in DateFormat
    ]
    assert all(f"</b> · {written}" in html for written in expected)
    assert len(set(expected)) == len(DateFormat)
