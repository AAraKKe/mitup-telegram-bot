"""Sender-phase behaviour on real Postgres: audience materialization, fan-out, finalization.

The sender's ``@db.with_session`` functions each open their own transaction and commit, so
everything they touch is committed cross-session state — the session fixture's uncommitted
seeds are invisible to them, and every test that provisions data does so in a committed
``db.begin()`` block and tears it down in a ``finally``. This file provisions rows only in the
997_5xx, 997_7xx and 997_8xx tg ranges — never in 6xx, which is owned by
``test_patreon_callback.py`` (the two operator tests keep their ``tg_base + 50`` operator ids in
8xx to stay clear of it). See the db-integration reference for the committed-range convention.
"""

import contextlib
import datetime as dt
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import cast

import pytest
from sqlalchemy import text
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession
from telegram import MessageEntity
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter
from telegram.ext import ExtBot

from mitup_bot import db
from mitup_bot.api_wrapper import BotAdapter, TelegramApi
from mitup_bot.events import broadcast as send_broadcasts
from mitup_bot.events.broadcast import MAX_BROADCAST_ATTEMPTS, MAX_DELIVERY_ATTEMPTS
from mitup_bot.models import Broadcast, BroadcastDelivery, BroadcastMessage, Settings, User
from mitup_bot.models.broadcasts import BroadcastDeliveryStatus, BroadcastStatus
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from mitup_bot.monitoring.backend import NullBackend
from mitup_bot.utils.entities import parse_format_tags
from mitup_bot.utils.messages import BroadcastOperatorMessages
from tests.helpers import make_test_metrics_client
from tests.helpers.monitoring import MetricAssertions

pytestmark = pytest.mark.db_test


@dataclass
class SentMessage:
    chat_id: int
    text: str
    entities: tuple[MessageEntity, ...]


# --- Fake bot recording every outbound send; can fail specific chat ids ---
class RecordingBot:
    def __init__(
        self,
        forbidden: set[int] | None = None,
        not_found: set[int] | None = None,
        generic_error: set[int] | None = None,
        network_error: set[int] | None = None,
        flood: set[int] | None = None,
    ):
        self.sent: list[SentMessage] = []
        self.forbidden = forbidden or set()
        self.not_found = not_found or set()
        self.generic_error = generic_error or set()
        self.network_error = network_error or set()
        self.flood = flood or set()

    async def send_message(
        self, *, chat_id: int, text: str, entities: list[MessageEntity] | None = None, **kwargs: object
    ) -> object:
        if chat_id in self.forbidden:
            raise Forbidden("Forbidden: bot was blocked by the user")
        if chat_id in self.not_found:
            raise BadRequest("Chat not found")
        if chat_id in self.network_error:
            raise NetworkError("Bad Gateway")
        if chat_id in self.flood:
            raise RetryAfter(20)
        if chat_id in self.generic_error:
            raise RuntimeError("unexpected serialization failure")
        self.sent.append(SentMessage(chat_id=chat_id, text=text, entities=tuple(entities or ())))
        return object()

    @property
    def sent_chat_ids(self) -> set[int]:
        return {message.chat_id for message in self.sent}

    def texts_to(self, chat_id: int) -> list[str]:
        return [message.text for message in self.sent if message.chat_id == chat_id]

    def messages_to(self, chat_id: int) -> list[SentMessage]:
        return [message for message in self.sent if message.chat_id == chat_id]


def make_api(bot: RecordingBot) -> TelegramApi:
    api = TelegramApi()
    api.adapter = BotAdapter(cast(ExtBot, bot), MetricsClient(NullBackend()))
    return api


# --- Provisioning committed fixtures ---
@dataclass
class SeedUser:
    tg_user_id: int
    language: str
    status: UserStatus = UserStatus.MEMBER


@dataclass
class SeedDelivery:
    tg_user_id: int
    status: BroadcastDeliveryStatus
    language_sent: str = "en"
    attempt_count: int = 0
    next_attempt_time: dt.datetime | None = None


@dataclass
class Provisioned:
    broadcast_id: int
    user_ids: dict[int, int]  # tg_user_id -> db user id


@contextlib.asynccontextmanager
async def provisioned_broadcast(
    tg_base: int,
    *,
    messages: dict[str, str],
    seed_users: list[SeedUser],
    status: BroadcastStatus = BroadcastStatus.QUEUED,
    attempts: int = 0,
    deliveries: list[SeedDelivery] | None = None,
) -> AsyncIterator[Provisioned]:
    async with db.begin() as session:
        broadcast = Broadcast(
            name="Integration Broadcast",
            author_tg_id=tg_base,
            status=status,
            attempts=attempts,
            messages=[BroadcastMessage(language=language, body_html=body) for language, body in messages.items()],
        )
        session.add(broadcast)
        await session.flush()

        user_ids: dict[int, int] = {}
        for seed in seed_users:
            user = User(first_name=f"Seed {seed.tg_user_id}", tg_user_id=seed.tg_user_id, status=seed.status)
            user.settings = Settings(language=seed.language)
            session.add(user)
            await session.flush()
            user_ids[seed.tg_user_id] = user.db_id

        # Flush each delivery on its own so ids are assigned in seed order — the claim reads
        # ORDER BY id, so tests that depend on delivery ordering need it to match the seed list.
        for delivery in deliveries or []:
            session.add(
                BroadcastDelivery(
                    broadcast_id=broadcast.db_id,
                    user_id=user_ids[delivery.tg_user_id],
                    language_sent=delivery.language_sent,
                    status=delivery.status,
                    attempt_count=delivery.attempt_count,
                    next_attempt_time=delivery.next_attempt_time,
                )
            )
            await session.flush()
        provisioned = Provisioned(broadcast_id=broadcast.db_id, user_ids=dict(user_ids))

    try:
        yield provisioned
    finally:
        await teardown_broadcast(provisioned.broadcast_id, tg_base)


async def teardown_broadcast(broadcast_id: int, tg_base: int):
    async with db.begin() as session:
        await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text("DELETE FROM broadcast_deliveries WHERE broadcast_id = :bid").bindparams(bid=broadcast_id)
        )
        await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text("DELETE FROM broadcasts WHERE id = :bid").bindparams(bid=broadcast_id)
        )
        # The anonymous sentinel (-1) is provisioned by one test outside the tg range; sweep it too.
        await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text(
                "DELETE FROM settings WHERE user_id IN "
                "(SELECT id FROM users WHERE tg_user_id BETWEEN :lo AND :hi OR tg_user_id = -1)"
            ).bindparams(lo=tg_base, hi=tg_base + 99)
        )
        await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text("DELETE FROM users WHERE tg_user_id BETWEEN :lo AND :hi OR tg_user_id = -1").bindparams(
                lo=tg_base, hi=tg_base + 99
            )
        )


# --- Query helpers reading committed state ---
async def fetch_deliveries(broadcast_id: int) -> list[BroadcastDelivery]:
    async with db.begin() as session:
        rows = await session.exec(
            select(BroadcastDelivery)
            .where(col(BroadcastDelivery.broadcast_id) == broadcast_id)
            .order_by(col(BroadcastDelivery.id))
        )
        return list(rows.all())


async def fetch_broadcast(broadcast_id: int) -> tuple[Broadcast, list[BroadcastMessage]]:
    async with db.begin() as session:
        broadcast = await session.get(Broadcast, broadcast_id)
        assert broadcast is not None
        return broadcast, list(broadcast.messages)


async def user_status(tg_user_id: int) -> UserStatus:
    async with db.begin() as session:
        user = (await session.exec(select(User).where(User.tg_user_id == tg_user_id))).one()
        return user.status


async def create_committed_user(tg_user_id: int, language: str, status: UserStatus):
    async with db.begin() as session:
        user = User(first_name=f"User {tg_user_id}", tg_user_id=tg_user_id, status=status)
        user.settings = Settings(language=language)
        session.add(user)
        await session.flush()


async def backdate_retry_deliveries(broadcast_id: int):
    """Pull every RETRY_PENDING row's next_attempt_time into the past so the next claim is due —
    simulating the flood window elapsing without a real sleep."""
    async with db.begin() as session:
        await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text(
                "UPDATE broadcast_deliveries SET next_attempt_time = now() - interval '1 minute' "
                "WHERE broadcast_id = :bid AND status = 'retry_pending'"
            ).bindparams(bid=broadcast_id)
        )


# --- Tests ---
async def test_resume_never_double_sends_already_sent_deliveries(db_session: AsyncSession):
    tg_base = 997_500
    already_sent, pending = tg_base, tg_base + 1
    async with provisioned_broadcast(
        tg_base,
        status=BroadcastStatus.SENDING,
        messages={"en": "<b>Hello</b>"},
        seed_users=[SeedUser(already_sent, "en"), SeedUser(pending, "en")],
        deliveries=[
            SeedDelivery(already_sent, BroadcastDeliveryStatus.SENT),
            SeedDelivery(pending, BroadcastDeliveryStatus.PENDING),
        ],
    ) as data:
        bot = RecordingBot()
        metrics = make_test_metrics_client()

        await send_broadcasts.send_all_pending(make_api(bot), metrics, data.broadcast_id, 2, {"en": "<b>Hello</b>"})

        # Only the PENDING recipient was contacted; the already-SENT one was never re-sent.
        assert bot.sent_chat_ids == {pending}
        statuses = {d.user_id: d.status for d in await fetch_deliveries(data.broadcast_id)}
        assert statuses[data.user_ids[already_sent]] is BroadcastDeliveryStatus.SENT
        assert statuses[data.user_ids[pending]] is BroadcastDeliveryStatus.SENT


async def test_unreachable_recipient_is_skipped_and_marked_left(db_session: AsyncSession):
    tg_base = 997_510
    blocked = tg_base
    async with provisioned_broadcast(
        tg_base,
        status=BroadcastStatus.SENDING,
        messages={"en": "hi"},
        seed_users=[SeedUser(blocked, "en")],
        deliveries=[SeedDelivery(blocked, BroadcastDeliveryStatus.PENDING)],
    ) as data:
        bot = RecordingBot(forbidden={blocked})
        metrics = make_test_metrics_client()

        await send_broadcasts.send_all_pending(make_api(bot), metrics, data.broadcast_id, 1, {"en": "hi"})

        deliveries = await fetch_deliveries(data.broadcast_id)
        assert [d.status for d in deliveries] == [BroadcastDeliveryStatus.SKIPPED_INACTIVE]
        assert await user_status(blocked) is UserStatus.LEFT
        MetricAssertions(metrics).assert_emitted(name=MetricKey.INACTIVE_USER_SET, value=1, unit=MetricUnit.COUNT)


async def test_language_without_message_falls_back_to_english(db_session: AsyncSession):
    tg_base = 997_520
    recipient = tg_base
    async with provisioned_broadcast(
        tg_base,
        messages={"en": "English body"},
        seed_users=[SeedUser(recipient, "de_DE")],  # no de_DE message provided
    ) as data:
        total = await send_broadcasts.materialize_audience(data.broadcast_id, ["en"])
        assert total == 1

        deliveries = await fetch_deliveries(data.broadcast_id)
        assert [d.language_sent for d in deliveries] == ["en"]

        bot = RecordingBot()
        await send_broadcasts.send_all_pending(
            make_api(bot), make_test_metrics_client(), data.broadcast_id, total, {"en": "English body"}
        )
        assert bot.texts_to(recipient) == ["English body"]


async def test_anonymous_invitee_never_receives_a_delivery(db_session: AsyncSession):
    tg_base = 997_530
    real = tg_base
    async with provisioned_broadcast(
        tg_base,
        messages={"en": "hi"},
        seed_users=[SeedUser(real, "en"), SeedUser(send_broadcasts.ANONYMOUS_INVITEE_TG_ID, "en")],
    ) as data:
        total = await send_broadcasts.materialize_audience(data.broadcast_id, ["en"])

        # Only the real member is enrolled; the -1 sentinel is excluded.
        assert total == 1
        deliveries = await fetch_deliveries(data.broadcast_id)
        enrolled_user_ids = {d.user_id for d in deliveries}
        assert enrolled_user_ids == {data.user_ids[real]}
        assert data.user_ids[send_broadcasts.ANONYMOUS_INVITEE_TG_ID] not in enrolled_user_ids


async def test_audience_materialization_is_idempotent(db_session: AsyncSession):
    tg_base = 997_540
    async with provisioned_broadcast(
        tg_base,
        messages={"en": "hi"},
        seed_users=[SeedUser(tg_base, "en"), SeedUser(tg_base + 1, "en")],
    ) as data:
        first_total = await send_broadcasts.materialize_audience(data.broadcast_id, ["en"])
        first_ids = {d.user_id for d in await fetch_deliveries(data.broadcast_id)}

        second_total = await send_broadcasts.materialize_audience(data.broadcast_id, ["en"])
        second_ids = {d.user_id for d in await fetch_deliveries(data.broadcast_id)}

        # The resumed call reports the frozen snapshot's total without re-inserting recipients.
        assert first_total == second_total == 2
        assert first_ids == second_ids
        broadcast, _ = await fetch_broadcast(data.broadcast_id)
        assert broadcast.total_recipients == 2


async def test_finalization_purges_deliveries_and_lands_counts(db_session: AsyncSession):
    tg_base = 997_550
    async with provisioned_broadcast(
        tg_base,
        messages={"en": "hi"},
        seed_users=[SeedUser(tg_base, "en"), SeedUser(tg_base + 1, "en")],
    ) as data:
        bot = RecordingBot()
        metrics = make_test_metrics_client()

        await send_broadcasts.run(make_api(bot), metrics, [])

        # Every recipient reached, the delivery table drained, counts rolled onto the parents.
        assert bot.sent_chat_ids == {tg_base, tg_base + 1}
        assert await fetch_deliveries(data.broadcast_id) == []
        broadcast, broadcast_messages = await fetch_broadcast(data.broadcast_id)
        assert broadcast.status is BroadcastStatus.DONE
        assert broadcast.sent_count == 2
        assert broadcast.total_recipients == 2
        assert {m.language: m.sent_count for m in broadcast_messages} == {"en": 2}
        # Delivery telemetry is now live per-delivery during the drain: both recipients emit a
        # one-hot SENT=1, and the single batch reports its throughput.
        assertions = MetricAssertions(metrics)
        assertions.assert_emitted(name=MetricKey.BROADCAST_DELIVERY_SENT, value=1, unit=MetricUnit.COUNT, times=2)
        assertions.assert_emitted(name=MetricKey.BROADCAST_BATCH_MESSAGES_SENT, value=2, unit=MetricUnit.COUNT)
        assertions.assert_emitted(name=MetricKey.BROADCAST_PROGRESS_PERCENT, value=100.0, unit=MetricUnit.PERCENT)


async def test_broadcast_beyond_attempt_threshold_is_failed(db_session: AsyncSession):
    tg_base = 997_750
    admin = tg_base + 50
    await create_committed_user(admin, "en", UserStatus.LEFT)  # reachable admin, not in the audience
    async with provisioned_broadcast(
        tg_base,  # provisioned_broadcast makes tg_base the author
        status=BroadcastStatus.SENDING,
        attempts=MAX_BROADCAST_ATTEMPTS,  # this run bumps it over the threshold
        messages={"en": "hi"},
        seed_users=[SeedUser(tg_base, "en")],  # the author is a reachable member
    ) as data:
        bot = RecordingBot()
        metrics = make_test_metrics_client()

        await send_broadcasts.run(make_api(bot), metrics, [admin])

        broadcast, _ = await fetch_broadcast(data.broadcast_id)
        assert broadcast.status is BroadcastStatus.FAILED
        # The failure summary goes to the author (author-first); no recipient was contacted, and the
        # admin fallback is never reached because the author was.
        expected = BroadcastOperatorMessages.SENDER_FAILED.get(
            lang="en",
            broadcast_id=data.broadcast_id,
            name=broadcast.name,
            attempts=MAX_BROADCAST_ATTEMPTS + 1,
            sent=0,
            failed=0,
            skipped=0,
        )
        assert bot.texts_to(tg_base) == [expected.text]
        assert admin not in bot.sent_chat_ids


async def test_summary_dm_reaches_the_author(db_session: AsyncSession):
    tg_base = 997_760
    author = tg_base
    recipient = tg_base + 1
    admin = tg_base + 50
    await create_committed_user(admin, "en", UserStatus.LEFT)
    async with provisioned_broadcast(
        tg_base,  # provisioned_broadcast makes tg_base the author
        messages={"en": "hi"},
        # The author has a user row but is LEFT, so they are not in the audience — keeping the body
        # and the summary cleanly separated. A distinct member is the sole recipient.
        seed_users=[SeedUser(author, "en", status=UserStatus.LEFT), SeedUser(recipient, "en")],
    ) as data:
        bot = RecordingBot()
        metrics = make_test_metrics_client()

        await send_broadcasts.run(make_api(bot), metrics, [admin])

        broadcast, _ = await fetch_broadcast(data.broadcast_id)
        summary = BroadcastOperatorMessages.SENDER_COMPLETE_SUMMARY.get(
            lang="en",
            broadcast_id=data.broadcast_id,
            name=broadcast.name,
            total=1,
            sent=1,
            failed=0,
            skipped=0,
            breakdown=BroadcastOperatorMessages.SENDER_BREAKDOWN_LINE.get(
                lang="en", language="en", sent=1, failed=0, skipped=0
            ),
        )
        # The author gets the summary; the recipient gets only the body; the admin gets nothing.
        assert bot.texts_to(author) == [summary.text]
        assert bot.texts_to(recipient) == ["hi"]
        assert admin not in bot.sent_chat_ids


async def test_summary_dm_falls_back_to_admins_when_the_author_has_no_user_row(db_session: AsyncSession):
    tg_base = 997_770
    recipient = tg_base + 1
    admin = tg_base + 50
    await create_committed_user(admin, "en", UserStatus.LEFT)
    async with provisioned_broadcast(
        tg_base,  # tg_base is the author but is deliberately never seeded a user row
        messages={"en": "hi"},
        seed_users=[SeedUser(recipient, "en")],
    ) as data:
        bot = RecordingBot()
        metrics = make_test_metrics_client()

        await send_broadcasts.run(make_api(bot), metrics, [admin])

        broadcast, _ = await fetch_broadcast(data.broadcast_id)
        summary = BroadcastOperatorMessages.SENDER_COMPLETE_SUMMARY.get(
            lang="en",
            broadcast_id=data.broadcast_id,
            name=broadcast.name,
            total=1,
            sent=1,
            failed=0,
            skipped=0,
            breakdown=BroadcastOperatorMessages.SENDER_BREAKDOWN_LINE.get(
                lang="en", language="en", sent=1, failed=0, skipped=0
            ),
        )
        # The author has no user row, so the summary falls back to the admin list.
        assert bot.texts_to(admin) == [summary.text]
        assert tg_base not in bot.sent_chat_ids


async def test_capped_generic_error_fails_delivery_permanently_but_broadcast_still_completes(
    db_session: AsyncSession,
):
    tg_base = 997_580
    failing, reachable = tg_base, tg_base + 1
    past = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
    async with provisioned_broadcast(
        tg_base,
        status=BroadcastStatus.SENDING,
        messages={"en": "hi"},
        seed_users=[SeedUser(failing, "en"), SeedUser(reachable, "en")],
        deliveries=[
            # Already on its final retry: the claim bumps it to the cap, so a transient error here
            # is now permanent — pinning that a genuinely FAILED delivery still lets the run finish.
            SeedDelivery(
                failing,
                BroadcastDeliveryStatus.RETRY_PENDING,
                attempt_count=MAX_DELIVERY_ATTEMPTS - 1,
                next_attempt_time=past,
            ),
            SeedDelivery(reachable, BroadcastDeliveryStatus.PENDING),
        ],
    ) as data:
        bot = RecordingBot(generic_error={failing})
        metrics = make_test_metrics_client()

        await send_broadcasts.send_all_pending(make_api(bot), metrics, data.broadcast_id, 2, {"en": "hi"})

        # The claim bumps the failing row to MAX_DELIVERY_ATTEMPTS, so its generic error resolves to
        # a permanent FAILED (not another retry); the reachable one resolves to SENT.
        statuses = {d.user_id: d.status for d in await fetch_deliveries(data.broadcast_id)}
        assert statuses[data.user_ids[failing]] is BroadcastDeliveryStatus.FAILED
        assert statuses[data.user_ids[reachable]] is BroadcastDeliveryStatus.SENT
        assert bot.sent_chat_ids == {reachable}
        # The permanent failure surfaces as a live one-hot FAILED=1 during the drain (not at
        # finalization, which no longer emits aggregate metrics).
        MetricAssertions(metrics).assert_emitted(
            name=MetricKey.BROADCAST_DELIVERY_FAILED, value=1, unit=MetricUnit.COUNT
        )

        # No author DM here: the empty admin list is the fallback, and this synthetic author id has
        # no user row, so finalization just rolls up counts and purges.
        await send_broadcasts.finalize_and_report(
            make_api(bot), metrics, [], tg_base, data.broadcast_id, BroadcastStatus.DONE
        )

        broadcast, broadcast_messages = await fetch_broadcast(data.broadcast_id)
        assert broadcast.status is BroadcastStatus.DONE
        assert broadcast.sent_count == 1
        assert broadcast.failed_count == 1
        assert {m.language: (m.sent_count, m.failed_count) for m in broadcast_messages} == {"en": (1, 1)}
        assert await fetch_deliveries(data.broadcast_id) == []


async def test_not_found_recipient_is_skipped_and_marked_left(db_session: AsyncSession):
    tg_base = 997_590
    gone = tg_base
    async with provisioned_broadcast(
        tg_base,
        status=BroadcastStatus.SENDING,
        messages={"en": "hi"},
        seed_users=[SeedUser(gone, "en")],
        deliveries=[SeedDelivery(gone, BroadcastDeliveryStatus.PENDING)],
    ) as data:
        bot = RecordingBot(not_found={gone})
        metrics = make_test_metrics_client()

        await send_broadcasts.send_all_pending(make_api(bot), metrics, data.broadcast_id, 1, {"en": "hi"})

        # BadRequest "not found" is a deleted account: skipped like a block, and the member is left.
        deliveries = await fetch_deliveries(data.broadcast_id)
        assert [d.status for d in deliveries] == [BroadcastDeliveryStatus.SKIPPED_INACTIVE]
        assert await user_status(gone) is UserStatus.LEFT
        MetricAssertions(metrics).assert_emitted(name=MetricKey.INACTIVE_USER_SET, value=1, unit=MetricUnit.COUNT)


async def test_network_error_leaves_broadcast_sending_for_the_next_run_to_finish(db_session: AsyncSession):
    tg_base = 997_700
    recipient = tg_base
    async with provisioned_broadcast(
        tg_base,
        status=BroadcastStatus.SENDING,
        messages={"en": "hi"},
        seed_users=[SeedUser(recipient, "en")],
    ) as data:
        failing_bot = RecordingBot(network_error={recipient})

        # A NetworkError is systemic: it propagates and aborts the run without finalizing.
        with pytest.raises(NetworkError):
            await send_broadcasts.run(make_api(failing_bot), make_test_metrics_client(), [])

        broadcast, _ = await fetch_broadcast(data.broadcast_id)
        assert broadcast.status is BroadcastStatus.SENDING  # left in flight for the next tick

        # The next tick resumes the same broadcast and drives it to a terminal state.
        healthy_bot = RecordingBot()
        await send_broadcasts.run(make_api(healthy_bot), make_test_metrics_client(), [])

        broadcast, _ = await fetch_broadcast(data.broadcast_id)
        assert broadcast.status is BroadcastStatus.DONE
        assert await fetch_deliveries(data.broadcast_id) == []


async def test_recipient_receives_their_own_language_body(db_session: AsyncSession):
    tg_base = 997_710
    # tg_base is the (unseeded) author, so the finalization summary falls back to the empty admin
    # list — no DM lands in the capture and the recipients' streams stay body-only. Recipients are
    # distinct ids.
    spanish, english = tg_base + 1, tg_base + 2
    async with provisioned_broadcast(
        tg_base,
        messages={"en": "EN body", "es_ES": "ES body"},
        seed_users=[SeedUser(spanish, "es_ES"), SeedUser(english, "en")],
    ) as data:
        bot = RecordingBot()

        await send_broadcasts.run(make_api(bot), make_test_metrics_client(), [])

        # Each recipient gets the body for their own language, not the English fallback.
        assert bot.texts_to(spanish) == ["ES body"]
        assert bot.texts_to(english) == ["EN body"]
        assert tg_base not in bot.sent_chat_ids
        broadcast, broadcast_messages = await fetch_broadcast(data.broadcast_id)
        assert broadcast.status is BroadcastStatus.DONE
        assert {m.language: m.sent_count for m in broadcast_messages} == {"en": 1, "es_ES": 1}


async def test_orphaned_deliveries_from_a_crashed_claim_are_counted_separately(db_session: AsyncSession):
    tg_base = 997_730
    orphaned_tg, sent_tg = tg_base, tg_base + 1
    async with provisioned_broadcast(
        tg_base,
        status=BroadcastStatus.SENDING,
        messages={"en": "hi"},
        seed_users=[SeedUser(orphaned_tg, "en"), SeedUser(sent_tg, "en")],
        deliveries=[
            # Simulates a worker that claimed this row (flipping it to IN_PROGRESS) and crashed
            # before recording its outcome.
            SeedDelivery(orphaned_tg, BroadcastDeliveryStatus.IN_PROGRESS),
            SeedDelivery(sent_tg, BroadcastDeliveryStatus.PENDING),
        ],
    ) as data:
        bot = RecordingBot()
        metrics = make_test_metrics_client()

        await send_broadcasts.send_all_pending(make_api(bot), metrics, data.broadcast_id, 2, {"en": "hi"})
        # The IN_PROGRESS row is never claimable again — only the still-PENDING one gets sent.
        assert bot.sent_chat_ids == {sent_tg}

        summary, _, _ = await send_broadcasts.finalize_broadcast(data.broadcast_id, BroadcastStatus.DONE)

        assert summary.sent == 1
        assert summary.orphaned == 1
        assert summary.failed == 0
        broadcast, broadcast_messages = await fetch_broadcast(data.broadcast_id)
        assert broadcast.orphan_count == 1
        assert broadcast.failed_count == 0
        assert {m.language: m.orphan_count for m in broadcast_messages} == {"en": 1}


async def test_terminal_failure_marks_never_attempted_pending_deliveries_as_failed(db_session: AsyncSession):
    tg_base = 997_740
    never_attempted = tg_base
    async with provisioned_broadcast(
        tg_base,
        status=BroadcastStatus.SENDING,
        messages={"en": "hi"},
        seed_users=[SeedUser(never_attempted, "en")],
        deliveries=[SeedDelivery(never_attempted, BroadcastDeliveryStatus.PENDING)],
    ) as data:
        # No send_all_pending call — mirrors the terminal-failure path, which skips draining
        # entirely and goes straight to finalization.
        summary, _, _ = await send_broadcasts.finalize_broadcast(data.broadcast_id, BroadcastStatus.FAILED)

        assert summary.failed == 1
        assert summary.sent == 0
        assert summary.orphaned == 0
        broadcast, broadcast_messages = await fetch_broadcast(data.broadcast_id)
        assert broadcast.failed_count == 1
        assert {m.language: m.failed_count for m in broadcast_messages} == {"en": 1}
        deliveries = await fetch_deliveries(data.broadcast_id)
        assert [d.status for d in deliveries] == [BroadcastDeliveryStatus.FAILED]


async def test_delivery_renders_body_as_the_same_formatted_text_the_preview_uses(db_session: AsyncSession):
    """Preview/delivery parity: the sender delivers `parse_format_tags(body_html, {})` — the exact
    FormattedText the preview builds — so an operator `<a href>` link arrives as a text_link entity."""
    tg_base = 997_720
    # tg_base is the (unseeded) author, so no summary DM lands in the recipient's capture. RecordingBot
    # records only text + entities (not the reply markup), so the Main Menu button does not affect
    # the preview-parity comparison below.
    recipient = tg_base + 1
    link_body = '<a href="https://mitup.social">join us</a>'
    async with provisioned_broadcast(
        tg_base,
        messages={"en": link_body},
        seed_users=[SeedUser(recipient, "en")],
    ) as data:
        bot = RecordingBot()

        await send_broadcasts.run(make_api(bot), make_test_metrics_client(), [])

        expected = parse_format_tags(link_body, {})
        delivered = bot.messages_to(recipient)
        assert len(delivered) == 1
        # Same visible text and same entities the preview would have shown — tags stripped, link kept.
        assert delivered[0].text == expected.text == "join us"
        assert list(delivered[0].entities) == expected.entities
        assert [(entity.type, entity.url) for entity in delivered[0].entities] == [
            ("text_link", "https://mitup.social")
        ]
        broadcast, _ = await fetch_broadcast(data.broadcast_id)
        assert broadcast.status is BroadcastStatus.DONE


async def test_claim_reclaims_pending_and_due_retry_but_skips_future_dated_retry(db_session: AsyncSession):
    tg_base = 997_800
    pending_tg, due_tg, future_tg = tg_base, tg_base + 1, tg_base + 2
    past = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
    future = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)
    async with provisioned_broadcast(
        tg_base,
        status=BroadcastStatus.SENDING,
        messages={"en": "hi"},
        seed_users=[SeedUser(pending_tg, "en"), SeedUser(due_tg, "en"), SeedUser(future_tg, "en")],
        deliveries=[
            SeedDelivery(pending_tg, BroadcastDeliveryStatus.PENDING),
            SeedDelivery(due_tg, BroadcastDeliveryStatus.RETRY_PENDING, attempt_count=1, next_attempt_time=past),
            SeedDelivery(future_tg, BroadcastDeliveryStatus.RETRY_PENDING, attempt_count=1, next_attempt_time=future),
        ],
    ) as data:
        batch = await send_broadcasts.claim_pending_batch(data.broadcast_id)

        # Only the PENDING and the due RETRY_PENDING rows are claimed; the future-dated one is not.
        claimed_attempts = {pending.user.db_id: pending.attempt_count for pending in batch}
        assert claimed_attempts == {data.user_ids[pending_tg]: 1, data.user_ids[due_tg]: 2}

        statuses = {
            delivery.user_id: (delivery.status, delivery.attempt_count)
            for delivery in await fetch_deliveries(data.broadcast_id)
        }
        # Claimed rows are IN_PROGRESS with an atomically bumped attempt_count.
        assert statuses[data.user_ids[pending_tg]] == (BroadcastDeliveryStatus.IN_PROGRESS, 1)
        assert statuses[data.user_ids[due_tg]] == (BroadcastDeliveryStatus.IN_PROGRESS, 2)
        # The future-dated retry was untouched.
        assert statuses[data.user_ids[future_tg]] == (BroadcastDeliveryStatus.RETRY_PENDING, 1)


async def test_transient_failure_parks_delivery_and_defers_finalization(db_session: AsyncSession):
    tg_base = 997_810
    failing = tg_base
    async with provisioned_broadcast(
        tg_base,
        status=BroadcastStatus.SENDING,
        attempts=3,  # a nonzero counter to prove the deferral reset
        messages={"en": "hi"},
        seed_users=[SeedUser(failing, "en")],
        deliveries=[SeedDelivery(failing, BroadcastDeliveryStatus.PENDING)],
    ) as data:
        bot = RecordingBot(generic_error={failing})

        await send_broadcasts.run(make_api(bot), make_test_metrics_client(), [])

        broadcast, _ = await fetch_broadcast(data.broadcast_id)
        # The generic error is transient: the delivery is parked RETRY_PENDING, the broadcast is
        # NOT finalized (stays SENDING), and its attempt counter is reset for the next tick.
        assert broadcast.status is BroadcastStatus.SENDING
        assert broadcast.attempts == 0
        deliveries = await fetch_deliveries(data.broadcast_id)
        assert [d.status for d in deliveries] == [BroadcastDeliveryStatus.RETRY_PENDING]
        assert deliveries[0].attempt_count == 1
        assert deliveries[0].next_attempt_time is not None


async def test_due_retry_is_resent_and_broadcast_finalizes(db_session: AsyncSession):
    tg_base = 997_820
    recipient = tg_base
    past = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
    async with provisioned_broadcast(
        tg_base,
        status=BroadcastStatus.SENDING,
        messages={"en": "hi"},
        seed_users=[SeedUser(recipient, "en")],
        deliveries=[
            SeedDelivery(recipient, BroadcastDeliveryStatus.RETRY_PENDING, attempt_count=1, next_attempt_time=past)
        ],
    ) as data:
        bot = RecordingBot()

        await send_broadcasts.run(make_api(bot), make_test_metrics_client(), [])

        # The due retry was re-sent and the broadcast drove itself to a clean DONE.
        assert bot.sent_chat_ids == {recipient}
        broadcast, _ = await fetch_broadcast(data.broadcast_id)
        assert broadcast.status is BroadcastStatus.DONE
        assert broadcast.sent_count == 1
        assert await fetch_deliveries(data.broadcast_id) == []


async def test_transient_failure_at_the_attempt_cap_fails_permanently(db_session: AsyncSession):
    tg_base = 997_830
    failing = tg_base
    past = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
    async with provisioned_broadcast(
        tg_base,
        status=BroadcastStatus.SENDING,
        messages={"en": "hi"},
        seed_users=[SeedUser(failing, "en")],
        deliveries=[
            SeedDelivery(
                failing,
                BroadcastDeliveryStatus.RETRY_PENDING,
                attempt_count=MAX_DELIVERY_ATTEMPTS - 1,
                next_attempt_time=past,
            )
        ],
    ) as data:
        bot = RecordingBot(generic_error={failing})

        await send_broadcasts.send_all_pending(
            make_api(bot), make_test_metrics_client(), data.broadcast_id, 1, {"en": "hi"}
        )

        # The claim bumps attempt_count to the cap; a transient failure there is now permanent.
        deliveries = await fetch_deliveries(data.broadcast_id)
        assert [(d.status, d.attempt_count) for d in deliveries] == [
            (BroadcastDeliveryStatus.FAILED, MAX_DELIVERY_ATTEMPTS)
        ]


async def test_flood_control_halts_mid_batch_releases_remainder_and_reclaims_after_window(db_session: AsyncSession):
    tg_base = 997_840
    # tg_base is the (unseeded) author, so the finalization summary on the second run falls back to
    # the empty admin list — no DM in the capture, keeping sent_chat_ids body-only. Recipients are
    # distinct ids.
    sent_first, flooded, released = tg_base + 1, tg_base + 2, tg_base + 3
    async with provisioned_broadcast(
        tg_base,
        status=BroadcastStatus.SENDING,
        attempts=2,  # a nonzero counter to prove the deferral reset
        messages={"en": "hi"},
        seed_users=[SeedUser(sent_first, "en"), SeedUser(flooded, "en"), SeedUser(released, "en")],
        deliveries=[
            SeedDelivery(sent_first, BroadcastDeliveryStatus.PENDING),
            SeedDelivery(flooded, BroadcastDeliveryStatus.PENDING),
            SeedDelivery(released, BroadcastDeliveryStatus.PENDING),
        ],
    ) as data:
        flooding_bot = RecordingBot(flood={flooded})

        await send_broadcasts.run(make_api(flooding_bot), make_test_metrics_client(), [])

        # Only the first recipient was contacted; flood control stopped the batch before the third.
        assert flooding_bot.sent_chat_ids == {sent_first}
        statuses = {d.user_id: (d.status, d.attempt_count) for d in await fetch_deliveries(data.broadcast_id)}
        assert statuses[data.user_ids[sent_first]] == (BroadcastDeliveryStatus.SENT, 1)
        # The flood-triggering row keeps its incremented attempt — a real API call happened.
        assert statuses[data.user_ids[flooded]] == (BroadcastDeliveryStatus.RETRY_PENDING, 1)
        # The untried row is released with its claim increment undone: attempt budget restored to 0.
        assert statuses[data.user_ids[released]] == (BroadcastDeliveryStatus.RETRY_PENDING, 0)
        # The broadcast is not finalized; it stays SENDING with its attempt counter reset.
        broadcast, _ = await fetch_broadcast(data.broadcast_id)
        assert broadcast.status is BroadcastStatus.SENDING
        assert broadcast.attempts == 0

        # After the flood window passes, a healthy run re-claims both retry rows and completes.
        await backdate_retry_deliveries(data.broadcast_id)
        healthy_bot = RecordingBot()

        await send_broadcasts.run(make_api(healthy_bot), make_test_metrics_client(), [])

        assert healthy_bot.sent_chat_ids == {flooded, released}
        broadcast, _ = await fetch_broadcast(data.broadcast_id)
        assert broadcast.status is BroadcastStatus.DONE
        assert await fetch_deliveries(data.broadcast_id) == []
