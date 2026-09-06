from sqlalchemy import BigInteger, UniqueConstraint
from sqlmodel import Field, SQLModel

from .base_model import BaseModel

# Name of the unique constraint on (meetup_id, position), so a duplicate position can be told
# apart from any other IntegrityError.
MEETING_IMAGES_UNIQUE_CONSTRAINT = "uq_meeting_images_meetup_id_position"


class MeetingImage(BaseModel, SQLModel, table=True):
    __tablename__ = "meeting_images"
    __table_args__ = (UniqueConstraint("meetup_id", "position", name=MEETING_IMAGES_UNIQUE_CONSTRAINT),)

    id: int | None = Field(default=None, primary_key=True)
    meetup_id: int | None = Field(
        default=None, foreign_key="meetups.id", ondelete="CASCADE", index=True, sa_type=BigInteger
    )
    # Where the photo sits in the banner, counting from zero.
    position: int = Field(nullable=False)
    # Telegram gives a photo two ids: `file_id` is the one a send or an edit references, and
    # `file_unique_id` is the short, stable one the card's `<img>` block uses as its media id.
    file_id: str = Field(nullable=False)
    file_unique_id: str = Field(nullable=False)

    def __hash__(self) -> int:
        return hash(self.model_dump_json(exclude={"id"}))

    def __eq__(self, other: object) -> bool:
        return hash(self) == hash(other) if isinstance(other, MeetingImage) else NotImplemented
