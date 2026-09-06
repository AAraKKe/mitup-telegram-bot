"""The photos a meeting shows: their slots, the order they come back in, and the layout column.

Every test works on its own meeting inside a savepoint it rolls back, so nothing it writes reaches
the seed data the rest of the suite shares.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot.handlers.meeting.edit.edit_meeting_images import drop_image
from mitup_bot.images import ImageLayout
from mitup_bot.models import MeetingImage, Meetup, User
from mitup_bot.utils import ButtonMessages
from mitup_bot.views import meeting as meeting_views

pytestmark = pytest.mark.db_test


def make_image(meetup_id: int | None, position: int) -> MeetingImage:
    return MeetingImage(
        meetup_id=meetup_id,
        position=position,
        file_id=f"file_id_{position}",
        file_unique_id=f"AQADunique{position}",
    )


async def make_meeting(session: AsyncSession, owner: User, title: str) -> Meetup:
    meeting = Meetup(
        title=title,
        waiting_list=False,
        public=False,
        allow_invitation=False,
        incognito=False,
        owner_id=owner.id,
    )
    session.add(meeting)
    await session.flush()
    return meeting


async def test_image_layout_defaults_to_collage_in_the_database(db_session: AsyncSession, seed_user: User):
    """The server default fills the column for rows inserted by code that does not set it."""
    savepoint = await db_session.begin_nested()
    try:
        await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
            text("INSERT INTO meetups (owner_id, title, active) VALUES (:owner, 'Layout Default', true)").bindparams(
                owner=seed_user.id
            )
        )
        stored = (
            await db_session.exec(  # type: ignore[call-overload]  # ty: ignore[no-matching-overload]  # https://github.com/fastapi/sqlmodel/issues/1657
                text("SELECT image_layout FROM meetups WHERE title = 'Layout Default'")
            )
        ).scalar_one()

        assert stored == ImageLayout.COLLAGE.value
    finally:
        await savepoint.rollback()


async def test_a_stored_layout_reads_back_as_its_enum(db_session: AsyncSession, seed_user: User):
    savepoint = await db_session.begin_nested()
    try:
        meeting = await make_meeting(db_session, seed_user, "Slideshow Meeting")
        meeting.image_layout = ImageLayout.SLIDESHOW
        await db_session.flush()
        await db_session.refresh(meeting)

        assert meeting.image_layout is ImageLayout.SLIDESHOW
    finally:
        await savepoint.rollback()


async def test_two_photos_cannot_share_a_slot_on_one_meeting(db_session: AsyncSession, seed_user: User):
    savepoint = await db_session.begin_nested()
    try:
        meeting = await make_meeting(db_session, seed_user, "Clashing Slots Meeting")

        with pytest.raises(IntegrityError):
            db_session.add_all([make_image(meeting.id, 0), make_image(meeting.id, 0)])
            await db_session.flush()
    finally:
        await savepoint.rollback()


async def test_the_same_slot_is_free_on_another_meeting(db_session: AsyncSession, seed_user: User):
    """The constraint is on the pair, so every meeting numbers its own photos from zero."""
    savepoint = await db_session.begin_nested()
    try:
        meeting = await make_meeting(db_session, seed_user, "First Photo Meeting")
        other_meeting = await make_meeting(db_session, seed_user, "Second Photo Meeting")

        db_session.add_all([make_image(meeting.id, 0), make_image(other_meeting.id, 0)])
        await db_session.flush()

        both = [meeting.id, other_meeting.id]
        stored = (
            await db_session.exec(
                select(MeetingImage).where(MeetingImage.position == 0, col(MeetingImage.meetup_id).in_(both))
            )
        ).all()
        assert {image.meetup_id for image in stored} == set(both)
    finally:
        await savepoint.rollback()


async def test_photos_come_back_in_position_order(db_session: AsyncSession, seed_user: User):
    """The relationship orders the photos by position, so no caller has to sort them."""
    savepoint = await db_session.begin_nested()
    try:
        meeting = await make_meeting(db_session, seed_user, "Ordered Photos Meeting")
        db_session.add_all([make_image(meeting.id, 2), make_image(meeting.id, 0), make_image(meeting.id, 1)])
        await db_session.flush()
        await db_session.refresh(meeting, ["images"])

        assert [image.position for image in meeting.images] == [0, 1, 2]
        assert [image.file_unique_id for image in meeting.images] == ["AQADunique0", "AQADunique1", "AQADunique2"]
    finally:
        await savepoint.rollback()


async def test_deleting_a_meeting_takes_its_photos_with_it(db_session: AsyncSession, seed_user: User):
    savepoint = await db_session.begin_nested()
    try:
        meeting = await make_meeting(db_session, seed_user, "Deleted Photo Meeting")
        db_session.add(make_image(meeting.id, 0))
        await db_session.flush()
        meeting_id = meeting.id

        await db_session.delete(meeting)
        await db_session.flush()

        assert (await db_session.exec(select(MeetingImage).where(MeetingImage.meetup_id == meeting_id))).all() == []
    finally:
        await savepoint.rollback()


async def test_dropping_a_photo_leaves_the_slots_contiguous(db_session: AsyncSession, seed_user: User):
    """Dropping a photo shifts the file ids of the photos after it down one row and deletes the last
    row, so no two rows ever hold the same position."""
    savepoint = await db_session.begin_nested()
    try:
        meeting = await make_meeting(db_session, seed_user, "Renumbered Photos Meeting")
        db_session.add_all([make_image(meeting.id, position) for position in range(4)])
        await db_session.flush()
        await db_session.refresh(meeting, ["images"])

        drop_image(meeting, 1)
        await db_session.flush()
        await db_session.refresh(meeting, ["images"])

        assert [image.position for image in meeting.images] == [0, 1, 2]
        assert [image.file_unique_id for image in meeting.images] == ["AQADunique0", "AQADunique2", "AQADunique3"]
        assert [image.file_id for image in meeting.images] == ["file_id_0", "file_id_2", "file_id_3"]
    finally:
        await savepoint.rollback()


async def test_a_freshly_created_meeting_renders_its_owner_card(db_session: AsyncSession, seed_user: User):
    """The owner card reads the images and the participants of a meeting that was inserted a
    moment ago, which the async engine cannot load on demand: the creation path loads both."""
    savepoint = await db_session.begin_nested()
    try:
        meeting = Meetup(
            title="Fresh",
            waiting_list=False,
            public=False,
            allow_invitation=False,
            incognito=False,
            owner=seed_user,
        )
        db_session.add(meeting)
        await db_session.flush()
        await db_session.refresh(meeting, ["joined_links", "images"])

        card = meeting_views.owner_view(meeting)

        assert card.photos == ()
        assert ButtonMessages.ADD_IMAGES.text(lang=seed_user.lang) in card.message.text
    finally:
        await savepoint.rollback()
