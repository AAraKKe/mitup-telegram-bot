import logging
import re

import pytest
from telegram import Update

from mitup_bot.handlers.main_menu.show_active_meetings import callback_query_show_meetings
from mitup_bot.models import User
from mitup_bot.utils import callbacks as cb
from tests.helpers import StubMitupContext, create_meetup, log_record
from tests.helpers.stub_db import MockDbSession


def request_page(context: StubMitupContext, page: int) -> None:
    match = re.match(cb.SHOW_ACTIVE_MEETING_PAGE.pattern, f"show;active_meeting_page:{page}")
    assert match is not None
    context.matches = [match]


async def test_active_list_records_its_counts_and_names_each_hidden_meeting(
    mock_session: MockDbSession,
    context: StubMitupContext,
    update: Update,
    user_with_settings: User,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    mock_session.add_object(user_with_settings, "tg_user_id")
    user_with_settings.meetups = [
        create_meetup(10),
        create_meetup(11, title="   "),
        create_meetup(12, active=False),
    ]
    request_page(context, 1)

    await callback_query_show_meetings(update, context)

    built = log_record(caplog, "Meeting list built")
    assert built.__dict__["list"] == "active"
    assert built.__dict__["total"] == 3
    assert built.__dict__["active"] == 2
    assert built.__dict__["listed"] == 1
    assert built.__dict__["dropped_blank_title"] == 1

    # The blank-title filter is the only code-level cause of "my meeting is not in my list", so the
    # meeting that vanished is named, not merely counted.
    hidden = log_record(caplog, "Meeting hidden from list")
    assert hidden.__dict__["meeting_id"] == 11
    assert hidden.__dict__["reason"] == "blank_title"


async def test_a_list_filtered_down_to_nothing_is_distinguishable_from_an_empty_one(
    mock_session: MockDbSession,
    context: StubMitupContext,
    update: Update,
    user_with_settings: User,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    mock_session.add_object(user_with_settings, "tg_user_id")
    user_with_settings.meetups = [create_meetup(10, active=False)]
    request_page(context, 1)

    await callback_query_show_meetings(update, context)

    empty = log_record(caplog, "Meeting list empty")
    assert empty.__dict__["reason"] == "no_matching_meetings"
    assert empty.__dict__["total"] == 1


async def test_a_page_the_list_no_longer_has_is_reported_only_when_it_moved(
    mock_session: MockDbSession,
    context: StubMitupContext,
    update: Update,
    user_with_settings: User,
    caplog: pytest.LogCaptureFixture,
):
    caplog.set_level(logging.INFO)
    mock_session.add_object(user_with_settings, "tg_user_id")
    user_with_settings.meetups = [create_meetup(10)]

    request_page(context, 1)
    await callback_query_show_meetings(update, context)
    assert "Meeting list page clamped" not in {record.message for record in caplog.records}

    request_page(context, 7)
    await callback_query_show_meetings(update, context)
    clamped = log_record(caplog, "Meeting list page clamped")
    assert clamped.__dict__["requested_page"] == 7
    assert clamped.__dict__["page"] == 1
    assert clamped.__dict__["reason"] == "page_out_of_range"
