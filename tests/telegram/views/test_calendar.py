"""The Calendar renders the date picker as a rich table with tappable day cells."""

import datetime as dt

import pytest

from mitup_bot.callback_data import DateCallbackData
from mitup_bot.views.calendar import Calendar, standalone
from mitup_bot.views.datetime_format import month_name, weekday_names

PICK = DateCallbackData(entity="cal", action="go", id=1)
NAV = DateCallbackData(entity="cal", action="nav", id=1)


def calendar(
    *,
    month: dt.date,
    selected: dt.date | None = None,
    today: dt.date,
    lang: str = "en",
) -> Calendar:
    return Calendar(month=month, selected=selected, today=today, pick_callback=PICK, nav_callback=NAV, lang=lang)


def test_table_holds_every_day_of_the_month_as_a_pick_button():
    markup = calendar(month=dt.date(2026, 6, 15), today=dt.date(2026, 6, 12)).table_markup()

    for day in range(1, 31):
        assert str(PICK.with_date(dt.date(2026, 6, day))) in markup
    # Days of the neighbouring months render as empty cells, not buttons.
    assert str(PICK.with_date(dt.date(2026, 5, 31))) not in markup
    assert str(PICK.with_date(dt.date(2026, 7, 1))) not in markup


def test_table_headers_are_the_weekdays_of_the_language_monday_first(lang: str):
    markup = calendar(month=dt.date(2026, 6, 15), today=dt.date(2026, 6, 12), lang=lang).table_markup()

    headers = "".join(f"<th>{standalone(name)}</th>" for name in weekday_names(lang))
    assert headers in markup


def test_selected_day_carries_the_success_accent():
    markup = calendar(
        month=dt.date(2026, 6, 15), selected=dt.date(2026, 6, 20), today=dt.date(2026, 6, 12)
    ).table_markup()

    assert markup.count('style="success"') == 1
    assert f'data="{PICK.with_date(dt.date(2026, 6, 20))}" style="success"' in markup


def test_today_carries_the_accent_until_a_selection_exists():
    markup = calendar(month=dt.date(2026, 6, 15), today=dt.date(2026, 6, 12)).table_markup()

    assert markup.count('style="success"') == 1
    assert f'data="{PICK.with_date(dt.date(2026, 6, 12))}" style="success"' in markup


def test_no_accent_when_the_marked_day_is_outside_the_rendered_month():
    markup = calendar(
        month=dt.date(2026, 7, 1), selected=dt.date(2026, 6, 20), today=dt.date(2026, 6, 12)
    ).table_markup()

    assert 'style="success"' not in markup


def test_month_nav_navigates_one_month_each_way(lang: str):
    markup = calendar(month=dt.date(2026, 6, 15), today=dt.date(2026, 5, 12), lang=lang).month_nav_markup()

    assert str(NAV.with_date(dt.date(2026, 5, 1))) in markup
    assert str(NAV.with_date(dt.date(2026, 7, 1))) in markup
    assert standalone(month_name(6, lang)) in markup


def test_month_back_arrow_disappears_at_the_current_month():
    markup = calendar(month=dt.date(2026, 6, 15), today=dt.date(2026, 6, 12)).month_nav_markup()

    assert str(NAV.with_date(dt.date(2026, 5, 1))) not in markup
    assert str(NAV.with_date(dt.date(2026, 7, 1))) in markup


def test_month_forward_at_december_crosses_into_the_next_year():
    markup = calendar(month=dt.date(2026, 12, 15), today=dt.date(2026, 6, 12)).month_nav_markup()

    assert str(NAV.with_date(dt.date(2027, 1, 1))) in markup


def test_year_nav_navigates_one_year_each_way():
    markup = calendar(month=dt.date(2027, 6, 15), today=dt.date(2026, 6, 12)).year_nav_markup()

    assert str(NAV.with_date(dt.date(2026, 6, 1))) in markup
    assert str(NAV.with_date(dt.date(2028, 6, 1))) in markup
    assert ">2027<" in markup


def test_year_back_arrow_disappears_at_the_current_year():
    markup = calendar(month=dt.date(2026, 12, 15), today=dt.date(2026, 6, 12)).year_nav_markup()

    assert str(NAV.with_date(dt.date(2025, 12, 1))) not in markup
    assert str(NAV.with_date(dt.date(2027, 12, 1))) in markup


def test_nav_labels_are_inert_chips(lang: str):
    content = calendar(month=dt.date(2026, 6, 15), today=dt.date(2026, 6, 12), lang=lang).content

    assert f'<tg-button type="disabled">{standalone(month_name(6, lang))}</tg-button>' in content.html
    assert '<tg-button type="disabled">2026</tg-button>' in content.html


def test_content_composes_table_then_month_then_year_rows():
    cal = calendar(month=dt.date(2026, 6, 15), today=dt.date(2026, 6, 12))
    html = cal.content.html

    table_at = html.index("<table compact>")
    month_at = html.index(standalone(month_name(6, "en")))
    year_at = html.index(">2026<")
    assert table_at < month_at < year_at


@pytest.mark.parametrize("name, expected", [("lun", "Lun"), ("junio", "Junio"), ("Mo", "Mo"), ("sáb.", "Sáb.")])
def test_a_standalone_name_starts_with_a_capital_and_keeps_the_rest(name: str, expected: str):
    assert standalone(name) == expected
