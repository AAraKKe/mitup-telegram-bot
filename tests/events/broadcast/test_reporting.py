from unittest import mock

import pytest
from telegram.error import TimedOut

from mitup_bot.events.broadcast import reporting
from mitup_bot.events.broadcast.types import LanguageBreakdown
from mitup_bot.exceptions import InactiveUserInteraction
from mitup_bot.models.broadcasts import BroadcastStatus
from mitup_bot.utils.entities import FormattedText, parse_format_tags
from mitup_bot.utils.messages import BroadcastOperatorMessages
from tests.events.broadcast.helpers import make_summary, script_exec
from tests.helpers import MockApi, MockDbSession, Result, create_member


def test_build_summary_view_failed_variant():
    summary = make_summary(status=BroadcastStatus.FAILED, name="Camp", attempts=6, sent=1, failed=2, skipped=3)

    view = reporting.build_summary_view(summary, "en")

    expected = BroadcastOperatorMessages.SENDER_FAILED.get(
        lang="en", broadcast_id=5, name="Camp", attempts=6, sent=1, failed=2, skipped=3
    )
    assert view.description == expected
    assert view.keyboard == []


def test_build_summary_view_complete_variant():
    breakdown = [LanguageBreakdown("en", 3, 1, 0)]
    summary = make_summary(
        status=BroadcastStatus.DONE, name="Camp", total=4, sent=3, failed=1, skipped=0, breakdown=breakdown
    )

    view = reporting.build_summary_view(summary, "en")

    expected_breakdown = FormattedText.join(
        "\n",
        [BroadcastOperatorMessages.SENDER_BREAKDOWN_LINE.get(lang="en", language="en", sent=3, failed=1, skipped=0)],
    )
    expected = BroadcastOperatorMessages.SENDER_COMPLETE_SUMMARY.get(
        lang="en", broadcast_id=5, name="Camp", total=4, sent=3, failed=1, skipped=0, breakdown=expected_breakdown
    )
    assert view.description == expected


@pytest.mark.parametrize(
    "status", [BroadcastStatus.FAILED, BroadcastStatus.DONE], ids=["failed_variant", "complete_variant"]
)
def test_build_summary_view_appends_orphan_warning_when_present(status: BroadcastStatus):
    summary = make_summary(status=status, name="Camp", attempts=6, sent=1, failed=2, skipped=3, orphaned=5)

    view = reporting.build_summary_view(summary, "en")

    assert BroadcastOperatorMessages.SENDER_ORPHANED_WARNING.get(lang="en", orphaned=5).text in view.description.text


def test_build_summary_view_failed_omits_orphan_warning_when_none():
    summary = make_summary(status=BroadcastStatus.FAILED, name="Camp", attempts=6, sent=1, failed=2, skipped=3)

    view = reporting.build_summary_view(summary, "en")

    expected = BroadcastOperatorMessages.SENDER_FAILED.get(
        lang="en", broadcast_id=5, name="Camp", attempts=6, sent=1, failed=2, skipped=3
    )
    assert view.description == expected


def test_build_summary_view_complete_omits_orphan_warning_when_none():
    breakdown = [LanguageBreakdown("en", 3, 1, 0)]
    summary = make_summary(
        status=BroadcastStatus.DONE, name="Camp", total=4, sent=3, failed=1, skipped=0, breakdown=breakdown
    )

    view = reporting.build_summary_view(summary, "en")

    expected_breakdown = FormattedText.join(
        "\n",
        [BroadcastOperatorMessages.SENDER_BREAKDOWN_LINE.get(lang="en", language="en", sent=3, failed=1, skipped=0)],
    )
    expected = BroadcastOperatorMessages.SENDER_COMPLETE_SUMMARY.get(
        lang="en", broadcast_id=5, name="Camp", total=4, sent=3, failed=1, skipped=0, breakdown=expected_breakdown
    )
    assert view.description == expected


def test_build_breakdown_line_uses_plain_variant_without_orphans():
    line = LanguageBreakdown(language="en", sent=3, failed=1, skipped=0, orphaned=0)

    result = reporting.build_breakdown_line(line, "en")

    expected = BroadcastOperatorMessages.SENDER_BREAKDOWN_LINE.get(
        lang="en", language="en", sent=3, failed=1, skipped=0
    )
    assert result == expected


def test_build_breakdown_line_uses_orphaned_variant_when_orphans_present():
    line = LanguageBreakdown(language="en", sent=3, failed=1, skipped=0, orphaned=2)

    result = reporting.build_breakdown_line(line, "en")

    expected = BroadcastOperatorMessages.SENDER_BREAKDOWN_LINE_WITH_ORPHANED.get(
        lang="en", language="en", sent=3, failed=1, skipped=0, orphaned=2
    )
    assert result == expected


@pytest.mark.parametrize(
    "template",
    [BroadcastOperatorMessages.SENDER_COMPLETE_SUMMARY, BroadcastOperatorMessages.SENDER_FAILED],
    ids=["complete", "failed"],
)
def test_sender_summary_templates_render_the_broadcast_id(template: BroadcastOperatorMessages):
    # Render straight from the source template (bypassing the translation catalog, whose English
    # msgstr is re-synced by the translation flow, not here) to prove the `#<id>` placeholder is
    # wired into both summary templates.
    rendered = parse_format_tags(
        template.value,
        {
            "broadcast_id": "77",
            "name": "C",
            "attempts": "1",
            "total": "1",
            "sent": "1",
            "failed": "0",
            "skipped": "0",
            "breakdown": "x",
        },
    )

    assert "#77" in rendered.text


async def test_load_operators_returns_found_and_skips_missing(mock_session: MockDbSession):
    operator = create_member(1, 500, "en")
    # First tg id resolves to a user; the second has no row.
    script_exec(mock_session, Result(results=(operator,)), Result())

    operators = await reporting.load_operators([500, 999])

    assert operators == {500: operator}


async def test_notify_operators_dms_the_author_and_never_the_admins(api: MockApi, monkeypatch: pytest.MonkeyPatch):
    author = create_member(1, 500, "en")
    monkeypatch.setattr(reporting, "resolve_author", mock.AsyncMock(return_value=author))
    load_operators = mock.AsyncMock()
    monkeypatch.setattr(reporting, "load_operators", load_operators)
    summary = make_summary(status=BroadcastStatus.DONE)

    await reporting.notify_operators(api, [500, 999], 500, summary)

    api.assert_send_message_to_user_called(user=author, view=reporting.build_summary_view(summary, "en"))
    # The author received the summary, so the admin fallback is never consulted.
    load_operators.assert_not_awaited()
    assert api.mock_method("send_message_to_user").await_count == 1


async def test_notify_operators_falls_back_to_admins_when_author_unreachable(
    api: MockApi, monkeypatch: pytest.MonkeyPatch
):
    author = create_member(1, 500, "en")
    admin = create_member(2, 999, "en")
    monkeypatch.setattr(reporting, "resolve_author", mock.AsyncMock(return_value=author))
    load_operators = mock.AsyncMock(return_value={999: admin})
    monkeypatch.setattr(reporting, "load_operators", load_operators)
    # The author DM fails; the fallback DM to the admin succeeds.
    api.mock_method("send_message_to_user").side_effect = [InactiveUserInteraction(500, private=True), None]
    summary = make_summary(status=BroadcastStatus.DONE)

    await reporting.notify_operators(api, [500, 999], 500, summary)

    # The fallback drops the author id we already failed on and DMs the remaining admins.
    load_operators.assert_awaited_once_with([999])
    assert api.mock_method("send_message_to_user").await_count == 2


async def test_notify_operators_falls_back_to_admins_on_any_author_send_error(
    api: MockApi, monkeypatch: pytest.MonkeyPatch
):
    # A non-Inactive error (here a TimedOut) must NOT escape and fault the already-terminal run — it
    # is swallowed and the summary falls back to the admins, same as an Inactive author.
    author = create_member(1, 500, "en")
    admin = create_member(2, 999, "en")
    monkeypatch.setattr(reporting, "resolve_author", mock.AsyncMock(return_value=author))
    load_operators = mock.AsyncMock(return_value={999: admin})
    monkeypatch.setattr(reporting, "load_operators", load_operators)
    api.mock_method("send_message_to_user").side_effect = [TimedOut(), None]
    summary = make_summary(status=BroadcastStatus.DONE)

    await reporting.notify_operators(api, [500, 999], 500, summary)

    load_operators.assert_awaited_once_with([999])
    # Two sends: the failed author attempt, then the admin fallback.
    assert api.mock_method("send_message_to_user").await_count == 2
    assert api.call_args_list("send_message_to_user")[-1].kwargs["user"] is admin


async def test_notify_operators_falls_back_when_author_has_no_user_row(api: MockApi, monkeypatch: pytest.MonkeyPatch):
    admin = create_member(2, 999, "en")
    monkeypatch.setattr(reporting, "resolve_author", mock.AsyncMock(return_value=None))
    load_operators = mock.AsyncMock(return_value={999: admin})
    monkeypatch.setattr(reporting, "load_operators", load_operators)
    summary = make_summary(status=BroadcastStatus.DONE)

    await reporting.notify_operators(api, [500, 999], 500, summary)

    load_operators.assert_awaited_once_with([999])
    api.assert_send_message_to_user_called(user=admin, view=reporting.build_summary_view(summary, "en"))
    assert api.mock_method("send_message_to_user").await_count == 1


async def test_notify_admins_continues_past_unreachable_operator(api: MockApi, monkeypatch: pytest.MonkeyPatch):
    reachable = create_member(1, 500, "en")
    unreachable = create_member(2, 501, "en")
    monkeypatch.setattr(reporting, "load_operators", mock.AsyncMock(return_value={500: reachable, 501: unreachable}))
    api.mock_method("send_message_to_user").side_effect = [None, InactiveUserInteraction(501, private=True)]
    summary = make_summary(status=BroadcastStatus.DONE)

    await reporting.notify_admins(api, [500, 501], summary)

    assert api.mock_method("send_message_to_user").await_count == 2
