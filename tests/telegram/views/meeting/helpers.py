import datetime as dt

from mitup_bot.emojis import Emojis
from mitup_bot.images import ImageLayout
from mitup_bot.keyboards import Keyboard
from mitup_bot.models import MeetingImage, Meetup, MeetupLocation
from mitup_bot.utils.messages import MeetingCardSectionMessages
from mitup_bot.views import meeting as meeting_views
from mitup_bot.views.meeting.sections import participants_count_line
from tests.helpers import create_joined_link, create_meetup, create_settings, create_user

OWNER_ID = 1
OWNER_NAME = "Owner"

BAR = MeetupLocation(name="The usual bar", coordinates=(2.34, 48.85))
# Coordinates are stored (longitude, latitude); the maps query reads them the other way round.
BAR_MAPS_URL = "https://www.google.com/maps/search/?api=1&query=48.85%2C2.34"


def owned_meeting(
    *,
    lang: str = "en",
    invitation: bool = False,
    incognito: bool = False,
    public: bool = False,
    guests: int = 0,
    waiting: int = 0,
    owner_joins: bool = False,
    owner_waits: bool = False,
    location: MeetupLocation | None = None,
) -> Meetup:
    """A meeting owned by `OWNER_NAME`, with the attendance the card is asked to render.

    Guests are named `Guest 0`, `Guest 1`, ... in join order, so a card's name lines can be
    matched one by one.
    """
    owner = create_user(id=OWNER_ID, first_name=OWNER_NAME, settings=create_settings(id=OWNER_ID, language=lang))
    meeting = create_meetup(
        id=7,
        title="Board game night",
        description="Bring snacks",
        owner=owner,
        language=lang,
        invitation=invitation,
        incognito=incognito,
        public=public,
        location=location,
    )
    if owner_joins or owner_waits:
        create_joined_link(owner, meeting, id=0, is_waiting_list=owner_waits)
    for index in range(guests + waiting):
        guest = create_user(id=index + 2, tg_user_id=index + 2, first_name=f"Guest {index}")
        create_joined_link(guest, meeting, id=index + 1, is_waiting_list=index >= guests)
    return meeting


def with_images(meeting: Meetup, count: int, layout: ImageLayout = ImageLayout.COLLAGE) -> Meetup:
    """Give *meeting* *count* photos, their ids named after their position."""
    meeting.image_layout = layout
    meeting.images = [
        MeetingImage(
            meetup_id=meeting.db_id,
            position=position,
            file_id=f"file-{position}",
            file_unique_id=f"uniq-{position}",
        )
        for position in range(count)
    ]
    return meeting


def populated_meeting(**kwargs) -> Meetup:
    """A meeting whose schedule and location both hold a value, so every section is titled."""
    meeting = owned_meeting(location=MeetupLocation(name="The usual bar", coordinates=(2.34, 48.85)), **kwargs)
    meeting.datetime = dt.datetime(2026, 9, 1, 18, 0, tzinfo=dt.UTC)
    meeting.end_datetime = dt.datetime(2026, 9, 1, 21, 0, tzinfo=dt.UTC)
    return meeting


def in_progress_meeting(*, locked: bool, **kwargs) -> Meetup:
    """A meeting whose start has passed and whose end has not, optionally locked while it runs."""
    now = dt.datetime.now(dt.UTC)
    meeting = owned_meeting(**kwargs)
    meeting.datetime = now - dt.timedelta(minutes=5)
    meeting.end_datetime = now + dt.timedelta(minutes=55)
    meeting.lock_on_start = locked
    assert meeting.is_in_progress
    return meeting


def body_lines(meeting: Meetup) -> list[str]:
    return meeting_views.owner_view(meeting).message.text.splitlines()


def attendee_lines(meeting: Meetup) -> list[str]:
    """The confirmed attendees' name lines in listing order, the owner's own row included and the
    waiting list's rows excluded."""
    lines = body_lines(meeting)
    waiting_at = next((index for index, line in enumerate(lines) if str(Emojis.WAITING) in line), len(lines))
    return [line for line in lines[:waiting_at] if line.startswith((OWNER_NAME, "Guest "))]


def section_header_line(title: MeetingCardSectionMessages, glyph: Emojis, meeting: Meetup) -> str:
    """One section title line as a reader sees it: the section's glyph, then its word."""
    return f"{glyph} {title.rich(lang=meeting.user_language).text}"


def participants_header_line(meeting: Meetup) -> str:
    """The Participants title line as a reader sees it, the count riding it behind a separator."""
    header = section_header_line(MeetingCardSectionMessages.PARTICIPANTS, Emojis.JOINED, meeting)
    return f"{header} · {participants_count_line(meeting).text}"


def url_buttons(keyboard: Keyboard) -> list[str]:
    return [button.url for row in keyboard for button in row if button.url is not None]
