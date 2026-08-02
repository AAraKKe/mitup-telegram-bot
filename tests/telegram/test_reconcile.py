from typing import cast
from unittest import mock

import pytest
from structlog.testing import capture_logs
from telegram.ext import ExtBot

from mitup_bot import reconcile
from mitup_bot.api_wrapper import ApiOutbox, BotAdapter
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricsClient
from tests.helpers import make_test_metrics_client
from tests.helpers.fixtures import create_user
from tests.helpers.stub_db import MockDbSession


def test_register_outbox_reconciler_wires_the_db_hook():
    with mock.patch("mitup_bot.db.set_outbox_reconciler") as set_reconciler:
        reconcile.register_outbox_reconciler()

    set_reconciler.assert_called_once_with(reconcile.reconcile_outbox)


@pytest.fixture
def reconcile_metrics_client() -> MetricsClient:
    return make_test_metrics_client()


@pytest.fixture
def adapter(reconcile_metrics_client: MetricsClient) -> BotAdapter:
    return BotAdapter(cast(ExtBot, mock.AsyncMock(spec=ExtBot)), reconcile_metrics_client)


async def test_reconcile_outbox_deletes_dead_messages(mock_session: MockDbSession, adapter: BotAdapter):
    outbox = ApiOutbox(dead_message_ids=[7, 9])

    await reconcile.reconcile_outbox(mock_session, adapter, outbox)

    delete_queries = [query for query in mock_session.queries_executed if query.startswith("DELETE FROM messages")]
    assert len(delete_queries) == 1
    assert "messages.id IN (7, 9)" in delete_queries[0]


async def test_reconcile_outbox_marks_unreachable_user(
    mock_session: MockDbSession, adapter: BotAdapter, reconcile_metrics_client: MetricsClient
):
    user = create_user(id=1, tg_user_id=555)
    mock_session.add_user(user)
    outbox = ApiOutbox(inactive_tg_user_ids=[555])

    with capture_logs() as logs:
        await reconcile.reconcile_outbox(mock_session, adapter, outbox)
    await reconcile_metrics_client.flush()

    assert user.status is UserStatus.LEFT
    changed = [entry for entry in logs if entry["event"] == "User status changed"]
    assert len(changed) == 1
    assert changed[0]["reason"] == "post_commit_fanout_unreachable"


async def test_reconcile_outbox_ignores_unknown_user(
    mock_session: MockDbSession, adapter: BotAdapter, reconcile_metrics_client: MetricsClient
):
    outbox = ApiOutbox(inactive_tg_user_ids=[999])

    with capture_logs() as logs:
        await reconcile.reconcile_outbox(mock_session, adapter, outbox)
    await reconcile_metrics_client.flush()

    # A tg user id with no matching row is a no-op: nothing to transition, nothing recorded.
    assert not [entry for entry in logs if entry["event"] == "User status changed"]
