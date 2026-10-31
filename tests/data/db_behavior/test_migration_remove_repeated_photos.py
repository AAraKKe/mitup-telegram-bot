"""The repair of meetings that hold one photo twice, run on real rows.

Every step is its own committed transaction, because `db_session` would hold the row locks of these
whole-table statements until the suite ends.
"""

import contextlib
import importlib.util
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

import mitup_bot.db
from mitup_bot import db
from mitup_bot.models import MeetingImage, Meetup, Settings, User
from mitup_bot.views import meeting as meeting_views

pytestmark = pytest.mark.db_test

MIGRATION_PATH = (
    # mitup_bot is a PEP 420 namespace package (no __file__); anchor on a concrete root module.
    Path(mitup_bot.db.__file__).parent / "migrations" / "versions" / "d06736ea31db_remove_repeated_meeting_photos.py"
)


def load_repair_statements() -> tuple[str, str, str]:
    """The statements `upgrade()` executes, in its order, read from the revision file because the
    versions directory is not an importable package."""
    spec = importlib.util.spec_from_file_location("migration_d06736ea31db", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return (
        module.DELETE_REPEATED_PHOTOS_SQL,
        module.MOVE_TO_NEGATIVE_SLOTS_SQL,
        module.SETTLE_NEGATIVE_SLOTS_SQL,
    )


REPAIR_STATEMENTS = load_repair_statements()

OWNER_TG_USER_ID = 997_950


@dataclass(frozen=True)
class StoredPhoto:
    row_id: int | None
    position: int
    file_unique_id: str
    file_id: str


def file_id_at(position: int) -> str:
    return f"file-at-{position}"


async def run_sql(session: AsyncSession, statement: str, **params: object):
    await session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
        text(statement).bindparams(**params)
    )


@contextlib.asynccontextmanager
async def committed_meetings(*banners: dict[int, str]) -> AsyncIterator[list[int]]:
    """Commit one meeting per banner, a map of position to `file_unique_id` inserted in the given
    order, and delete everything afterwards."""
    async with db.begin() as session:
        owner = User(first_name="Repeated Photos Owner", tg_user_id=OWNER_TG_USER_ID, settings=Settings())
        meetings = [
            Meetup(
                title="Repeated Photos Meeting",
                waiting_list=False,
                public=False,
                allow_invitation=False,
                incognito=False,
                owner=owner,
            )
            for _ in banners
        ]
        session.add(owner)
        session.add_all(meetings)
        await session.flush()
        meeting_ids = [meeting.db_id for meeting in meetings]
        for meeting_id, banner in zip(meeting_ids, banners, strict=True):
            for position, file_unique_id in banner.items():
                session.add(
                    MeetingImage(
                        meetup_id=meeting_id,
                        position=position,
                        file_id=file_id_at(position),
                        file_unique_id=file_unique_id,
                    )
                )
                await session.flush()
    try:
        yield meeting_ids
    finally:
        async with db.begin() as session:
            await run_sql(session, "DELETE FROM meeting_images WHERE meetup_id = ANY(:ids)", ids=meeting_ids)
            await run_sql(session, "DELETE FROM meetups WHERE id = ANY(:ids)", ids=meeting_ids)
            await run_sql(session, "DELETE FROM users WHERE tg_user_id = :tg_user_id", tg_user_id=OWNER_TG_USER_ID)


async def run_repair(*disabled_planner_methods: str):
    async with db.begin() as session:
        # A lock held by another test fails here instead of hanging the run.
        await run_sql(session, "SET LOCAL lock_timeout = '5s'")
        for planner_method in disabled_planner_methods:
            await run_sql(session, f"SET LOCAL {planner_method} = off")
        for statement in REPAIR_STATEMENTS:
            await run_sql(session, statement)


async def stored_photos(meeting_id: int) -> list[StoredPhoto]:
    async with db.begin() as session:
        images = (
            await session.exec(
                select(MeetingImage).where(MeetingImage.meetup_id == meeting_id).order_by(col(MeetingImage.position))
            )
        ).all()
    return [StoredPhoto(image.id, image.position, image.file_unique_id, image.file_id) for image in images]


async def test_a_photo_held_three_times_keeps_the_copy_at_its_lowest_position(db_session: AsyncSession):
    async with committed_meetings({0: "A", 1: "B", 2: "A", 3: "C", 4: "A"}) as (meeting_id,):
        before = await stored_photos(meeting_id)

        await run_repair()

        assert await stored_photos(meeting_id) == [
            StoredPhoto(before[0].row_id, 0, "A", file_id_at(0)),
            StoredPhoto(before[1].row_id, 1, "B", file_id_at(1)),
            StoredPhoto(before[3].row_id, 2, "C", file_id_at(3)),
        ]


async def test_the_survivor_is_the_lowest_position_and_not_the_oldest_row(db_session: AsyncSession):
    async with committed_meetings({3: "A", 2: "C", 1: "A", 0: "B"}) as (meeting_id,):
        before = await stored_photos(meeting_id)

        await run_repair()

        assert await stored_photos(meeting_id) == [
            StoredPhoto(before[0].row_id, 0, "B", file_id_at(0)),
            StoredPhoto(before[1].row_id, 1, "A", file_id_at(1)),
            StoredPhoto(before[2].row_id, 2, "C", file_id_at(2)),
        ]


@pytest.mark.parametrize(
    "banner",
    [{4: "C", 2: "B", 0: "A"}, {3: "C", 2: "B", 1: "A"}],
    ids=["gaps_between_photos", "every_target_slot_held_by_a_row_yet_to_move"],
)
async def test_a_meeting_with_gaps_and_no_repeats_is_closed_up(db_session: AsyncSession, banner: dict[int, str]):
    """Inserting the highest position first and forcing a merge join on row id makes the update reach
    the rows in the order that breaks the unique constraint if each one moves straight to its target."""
    async with committed_meetings(banner) as (meeting_id,):
        before = await stored_photos(meeting_id)

        await run_repair("enable_hashjoin", "enable_nestloop")

        assert await stored_photos(meeting_id) == [
            StoredPhoto(photo.row_id, slot, photo.file_unique_id, photo.file_id) for slot, photo in enumerate(before)
        ]


async def test_a_clean_meeting_is_untouched(db_session: AsyncSession):
    async with committed_meetings({0: "A", 1: "B", 2: "C"}) as (meeting_id,):
        before = await stored_photos(meeting_id)

        await run_repair()

        assert await stored_photos(meeting_id) == before


async def test_the_same_photo_on_two_meetings_stays_on_both(db_session: AsyncSession):
    async with committed_meetings({0: "A", 1: "B"}, {0: "B", 1: "A"}) as (first_id, second_id):
        first_before = await stored_photos(first_id)
        second_before = await stored_photos(second_id)

        await run_repair()

        assert await stored_photos(first_id) == first_before
        assert await stored_photos(second_id) == second_before


async def test_a_repaired_meeting_renders_a_card_with_no_repeated_media_id(db_session: AsyncSession):
    async with committed_meetings({0: "A", 1: "B", 2: "A", 3: "C", 4: "A"}) as (meeting_id,):
        await run_repair()

        async with db.begin() as session:
            meeting = await Meetup.by_id(session, meeting_id)
            assert meeting is not None
            card = meeting_views.owner_view(meeting)

        assert [photo.media_id for photo in card.photos] == ["A", "B", "C"]
