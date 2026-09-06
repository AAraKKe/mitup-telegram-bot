import datetime as dt
from collections.abc import Callable
from dataclasses import replace

import pytest

from mitup_bot.datetimes import DateFormat
from mitup_bot.emojis import Emojis
from mitup_bot.models import Meetup
from mitup_bot.translations import SUPPORTED_LANGUAGES
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingEditSettingsMessages, SettingsMessages
from mitup_bot.views import MitupView, factory
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.datetime_format import localized_datetime
from mitup_bot.views.factory import LANGUAGE_BUTTONS
from mitup_bot.views.settings_sections import SAMPLE_MOMENT
from tests.helpers.rich_transport import BUTTON_ROW_RE
from tests.telegram.views.meeting.helpers import owned_meeting

MADRID = "Europe/Madrid"


def card_html(meeting: Meetup) -> str:
    return meeting_views.settings_view(meeting).message.html


def behavior_html(meeting: Meetup) -> str:
    return meeting_views.behavior_view(meeting).message.html


def time_format_html(meeting: Meetup) -> str:
    return meeting_views.time_format_view(meeting).message.html


def test_settings_card_opens_on_its_title_then_the_language_section(lang: str):
    meeting = owned_meeting(lang=lang)

    html = card_html(meeting)

    assert html.startswith(f"<h2>{ButtonMessages.SETTINGS.text(lang=lang)}</h2>")
    language_header = html.index(ButtonMessages.LANGUAGE.text(lang=lang))
    behavior = html.index(MeetingEditSettingsMessages.BEHAVIOR_GROUP.rich(lang=lang).text)

    assert language_header < behavior


def test_settings_card_carries_one_button_per_supported_language(lang: str):
    meeting = owned_meeting(lang=lang)

    html = card_html(meeting)

    assert all(LANGUAGE_BUTTONS[code].text(lang=lang) in html for code in SUPPORTED_LANGUAGES)
    assert all(
        str(cb.SET_MEETING_LANGUAGE.with_ids(meeting_id=meeting.db_id, id=index)) in html
        for index in range(len(SUPPORTED_LANGUAGES))
    )


def test_the_language_buttons_are_full_width_rows_grouped_like_the_user_picker(lang: str):
    """An inline chip is drawn at a size the message cannot influence, so the languages are rows.

    They are grouped exactly as the user's own picker groups them, so the same grid reads the same
    way on both screens.
    """
    meeting = owned_meeting(lang=lang)
    picker_rows = BUTTON_ROW_RE.findall(
        factory.language_grid_content(lang, current=lang, callback_data=cb.SET_LANGUAGE).html
    )

    html = card_html(meeting)
    language_group = html[: html.index(MeetingEditSettingsMessages.BEHAVIOR_GROUP.rich(lang=lang).text)]
    rows = BUTTON_ROW_RE.findall(language_group)

    assert len(rows) == len(picker_rows)
    assert [row.count("<tg-button ") for row in rows] == [row.count("<tg-button ") for row in picker_rows]


def test_the_meetings_own_language_button_is_the_accented_one():
    meeting = owned_meeting()
    meeting.language = "es_ES"
    picked = str(cb.SET_MEETING_LANGUAGE.with_ids(meeting_id=meeting.db_id, id=SUPPORTED_LANGUAGES.index("es_ES")))

    html = card_html(meeting)

    assert f'data="{picked}" style="primary"' in html


def test_the_card_offers_the_two_sub_cards_with_a_line_saying_what_each_holds(lang: str):
    meeting = owned_meeting(lang=lang)

    html = card_html(meeting)
    behavior = MeetingEditSettingsMessages.BEHAVIOR_GROUP.rich(lang=lang).text
    time_format = MeetingEditSettingsMessages.TIME_FORMAT_GROUP.rich(lang=lang).text

    assert f"{Emojis.CONTROLS} <b>{behavior}</b>" in html
    assert f"{Emojis.CLOCK} <b>{time_format}</b>" in html
    assert MeetingEditSettingsMessages.BEHAVIOR_LINE.rich(lang=lang).html in html
    assert MeetingEditSettingsMessages.TIME_FORMAT_LINE.rich(lang=lang).html in html
    assert str(cb.OPEN_MEETING_BEHAVIOR.with_id(meeting.db_id)) in html
    assert str(cb.OPEN_MEETING_TIME_FORMAT.with_id(meeting.db_id)) in html


def test_the_card_holds_no_toggle_of_its_own():
    """Every setting lives on a sub-card, so the card itself flips nothing."""
    meeting = owned_meeting()
    toggles = [
        cb.SET_MEETING_WAITING_LIST,
        cb.SET_MEETING_PUBLIC,
        cb.SET_MEETING_ALLOW_INVITATIONS,
        cb.SET_MEETING_INCOGNITO,
        cb.SET_MEETING_LOCK_ON_START,
        cb.SET_MEETING_SHOW_TIMEZONE,
        cb.SET_MEETING_CLOCK_24H,
    ]

    html = card_html(meeting)

    assert not any(str(toggle.with_id(meeting.db_id)) in html for toggle in toggles)


def test_the_card_closes_on_the_way_back_to_the_meeting(lang: str):
    meeting = owned_meeting(lang=lang)

    view = meeting_views.settings_view(meeting)

    assert view.menu[-1][0].callback_data == cb.EDIT_MEETING.with_id(meeting.db_id)


def test_the_behavior_sub_card_titles_itself_and_says_the_states_are_tappable(lang: str):
    meeting = owned_meeting(lang=lang)

    html = behavior_html(meeting)
    title = MeetingEditSettingsMessages.BEHAVIOR_GROUP.rich(lang=lang).text

    assert html.startswith(f"<h2>{Emojis.CONTROLS} {title}</h2>")
    assert SettingsMessages.TOGGLE_HINT.rich(lang=lang).html in html


def test_the_behavior_sub_card_carries_every_behaviour_toggle():
    meeting = owned_meeting()
    toggles = [
        cb.SET_MEETING_WAITING_LIST,
        cb.SET_MEETING_PUBLIC,
        cb.SET_MEETING_ALLOW_INVITATIONS,
        cb.SET_MEETING_INCOGNITO,
        cb.SET_MEETING_LOCK_ON_START,
    ]

    html = behavior_html(meeting)

    assert all(str(toggle.with_id(meeting.db_id)) in html for toggle in toggles)


def test_every_behaviour_setting_states_what_it_does(lang: str):
    meeting = owned_meeting(lang=lang)

    html = behavior_html(meeting)
    explanations = [
        MeetingEditSettingsMessages.WAITING_LIST_EXPLANATION,
        MeetingEditSettingsMessages.PUBLIC_EXPLANATION,
        MeetingEditSettingsMessages.OPEN_INVITATIONS_EXPLANATION,
        MeetingEditSettingsMessages.INCOGNITO_EXPLANATION,
        MeetingEditSettingsMessages.LOCK_ON_START_EXPLANATION,
    ]

    assert all(explanation.rich(lang=lang).html in html for explanation in explanations)


def test_the_time_format_sub_card_titles_itself_and_says_the_states_are_tappable(lang: str):
    meeting = owned_meeting(lang=lang)

    html = time_format_html(meeting)
    title = MeetingEditSettingsMessages.TIME_FORMAT_GROUP.rich(lang=lang).text

    assert html.startswith(f"<h2>{Emojis.CLOCK} {title}</h2>")
    assert SettingsMessages.TOGGLE_HINT.rich(lang=lang).html in html


def test_every_time_format_setting_states_what_it_does(lang: str):
    meeting = owned_meeting(lang=lang)

    html = time_format_html(meeting)

    assert ButtonMessages.SHOW_TIMEZONE.text(lang=lang) in html
    assert ButtonMessages.CLOCK_24H.text(lang=lang) in html
    assert str(cb.SET_MEETING_SHOW_TIMEZONE.with_id(meeting.db_id)) in html
    assert str(cb.SET_MEETING_CLOCK_24H.with_id(meeting.db_id)) in html
    assert MeetingEditSettingsMessages.SHOW_TIMEZONE_EXPLANATION.rich(lang=lang).html in html
    assert MeetingEditSettingsMessages.CLOCK_24H_EXPLANATION.rich(lang=lang).html in html
    assert MeetingEditSettingsMessages.DATE_FORMAT_EXPLANATION.rich(lang=lang).html in html


@pytest.mark.parametrize(
    "sub_card",
    [meeting_views.behavior_view, meeting_views.time_format_view],
    ids=["behavior", "time_format"],
)
def test_each_sub_card_closes_on_the_way_back_to_the_settings_card(sub_card: Callable[[Meetup], MitupView], lang: str):
    meeting = owned_meeting(lang=lang)

    back = sub_card(meeting).menu[-1][0]

    assert back.callback_data == cb.EDIT_MEETING_SETTINGS.with_id(meeting.db_id)
    assert back.text == ButtonMessages.SETTINGS.back(lang=lang)


def test_the_date_format_offers_every_format_as_a_chip_on_its_own_line(lang: str):
    meeting = owned_meeting(lang=lang)

    html = time_format_html(meeting)

    assert all(
        str(cb.SET_MEETING_DATE_FORMAT.with_ids(meeting_id=meeting.db_id, id=index)) in html
        for index in range(len(DateFormat))
    )
    assert all(
        label.text(lang=lang) in html
        for label in [
            ButtonMessages.DATE_FORMAT_DEFAULT,
            ButtonMessages.DATE_FORMAT_LONG,
            ButtonMessages.DATE_FORMAT_FULL,
        ]
    )


@pytest.mark.parametrize("date_format", list(DateFormat))
def test_the_date_format_in_force_is_the_accented_chip(date_format: DateFormat):
    meeting = owned_meeting()
    meeting.date_format = date_format
    picked = str(cb.SET_MEETING_DATE_FORMAT.with_ids(meeting_id=meeting.db_id, id=list(DateFormat).index(date_format)))

    html = time_format_html(meeting)

    assert f'data="{picked}" style="primary"' in html
    assert html.count('style="primary"') == 1


def previews(moment: dt.datetime, meeting: Meetup, *, lang: str, created: dt.datetime | None) -> list[str]:
    """The meeting's moment written under each format, with the card's other two settings applied."""
    return [
        localized_datetime(
            moment,
            lang=lang,
            tz=meeting.timezone,
            created=created,
            time_format=replace(meeting.time_format, date_format=date_format),
        )
        for date_format in DateFormat
    ]


def test_every_date_format_previews_the_meetings_own_start():
    meeting = owned_meeting()
    meeting.owner.settings.timezone = MADRID
    meeting.datetime = dt.datetime(2027, 3, 17, 21, 45, tzinfo=dt.UTC)
    meeting.clock_24h = False

    html = time_format_html(meeting)

    expected = previews(meeting.datetime, meeting, lang=meeting.lang, created=meeting.created_time)
    assert all(f"</b> · {written}" in html for written in expected)
    assert len(set(expected)) == len(DateFormat)


def test_a_meeting_with_no_start_yet_previews_a_sample_moment():
    meeting = owned_meeting()
    meeting.datetime = None

    html = time_format_html(meeting)

    expected = previews(SAMPLE_MOMENT, meeting, lang=meeting.lang, created=None)
    assert all(f"</b> · {written}" in html for written in expected)


def test_the_previews_are_written_in_the_meetings_language_not_the_owners():
    """The dates the setting decides are read by everyone the meeting reaches, in its language."""
    meeting = owned_meeting(lang="en")
    meeting.owner.settings.language = "en"
    meeting.language = "es_ES"

    html = time_format_html(meeting)

    expected = previews(SAMPLE_MOMENT, meeting, lang="es_ES", created=None)
    assert all(f"</b> · {written}" in html for written in expected)
