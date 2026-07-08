from unittest import mock

import pytest

from mitup_bot.cli.broadcast import reporting
from mitup_bot.cli.broadcast.types import LanguageBreakdown
from mitup_bot.exceptions import InactiveUserInteraction
from mitup_bot.models.broadcasts import BroadcastStatus
from mitup_bot.utils.entities import FormattedText
from mitup_bot.utils.messages import BroadcastOperatorMessages
from tests.cli.broadcast.helpers import make_summary, script_exec
from tests.helpers import MockApi, MockDbSession, Result, create_member


def test_build_summary_view_failed_variant():
    summary = make_summary(status=BroadcastStatus.FAILED, name="Camp", attempts=6, sent=1, failed=2, skipped=3)

    view = reporting.build_summary_view(summary, "en")

    expected = BroadcastOperatorMessages.SENDER_FAILED.get(
        lang="en", name="Camp", attempts=6, sent=1, failed=2, skipped=3
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
        lang="en", name="Camp", total=4, sent=3, failed=1, skipped=0, breakdown=expected_breakdown
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
        lang="en", name="Camp", attempts=6, sent=1, failed=2, skipped=3
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
        lang="en", name="Camp", total=4, sent=3, failed=1, skipped=0, breakdown=expected_breakdown
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


async def test_load_operators_returns_found_and_skips_missing(mock_session: MockDbSession):
    operator = create_member(1, 500, "en")
    # First tg id resolves to a user; the second has no row.
    script_exec(mock_session, Result(results=(operator,)), Result())

    operators = await reporting.load_operators([500, 999])

    assert operators == {500: operator}


async def test_notify_operators_continues_past_unreachable_operator(api: MockApi, monkeypatch: pytest.MonkeyPatch):
    reachable = create_member(1, 500, "en")
    unreachable = create_member(2, 501, "en")
    monkeypatch.setattr(reporting, "load_operators", mock.AsyncMock(return_value={500: reachable, 501: unreachable}))
    api.mock_method("send_message_to_user").side_effect = [None, InactiveUserInteraction(501, private=True)]
    summary = make_summary(status=BroadcastStatus.DONE)

    await reporting.notify_operators(api, [500, 501], summary)

    assert api.mock_method("send_message_to_user").await_count == 2
