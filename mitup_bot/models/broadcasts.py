import datetime as dt
from enum import StrEnum

from sqlalchemy import Column, DateTime, Enum, FetchedValue, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel

from .base_model import BaseModel


class BroadcastStatus(StrEnum):
    """Lifecycle state of a `Broadcast` row.

    DRAFT is uploaded but unconfirmed; QUEUED is confirmed and awaiting the sender; SENDING is a
    run in progress; DONE, FAILED and CANCELLED are terminal. FAILED is reached once `attempts`
    crosses the retry threshold.
    """

    DRAFT = "draft"
    QUEUED = "queued"
    SENDING = "sending"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BroadcastDeliveryStatus(StrEnum):
    """Per-recipient delivery state, resolved as the sender fans a broadcast out.

    PENDING rows are the not-yet-attempted work queue; IN_PROGRESS is a batch claimed by a worker
    whose real outcome is not yet known (crashing here orphans the row — see the sender module
    docstring); SENT, SKIPPED_INACTIVE (recipient unreachable) and FAILED are terminal outcomes
    rolled up into the parent counts at finalization.
    """

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SENT = "sent"
    SKIPPED_INACTIVE = "skipped_inactive"
    FAILED = "failed"


class Broadcast(BaseModel, SQLModel, table=True):
    __tablename__: str = "broadcasts"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    # native_enum=False keeps the column a plain VARCHAR while coercing loaded rows back to
    # BroadcastStatus, so `status is BroadcastStatus.X` checks hold everywhere a row is loaded.
    status: BroadcastStatus = Field(
        default=BroadcastStatus.DRAFT,
        sa_column=Column(
            Enum(BroadcastStatus, native_enum=False, length=16, values_callable=lambda enum: [m.value for m in enum]),
            nullable=False,
            server_default=BroadcastStatus.DRAFT.value,
        ),
    )
    author_tg_id: int
    # Bumped once per send run; the sender declares a terminal FAILED after a threshold.
    attempts: int = 0
    total_recipients: int | None = None
    sent_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    # Deliveries left IN_PROGRESS by a worker that crashed between claiming and recording outcomes.
    orphan_count: int = 0
    # App-set (not trigger-managed): the sender stamps these as the run progresses.
    sending_started_time: dt.datetime | None = None
    completed_time: dt.datetime | None = None
    created_time: dt.datetime | None = Field(default=None, sa_column=Column(DateTime, server_default=FetchedValue()))
    updated_time: dt.datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, server_default=FetchedValue(), server_onupdate=FetchedValue()),
    )

    # lazy="selectin": the small per-broadcast language set is traversed in plain Python for
    # preview and send, and implicit lazy loads raise MissingGreenlet under the async engine.
    messages: list[BroadcastMessage] = Relationship(
        back_populates="broadcast",
        cascade_delete=True,
        sa_relationship_kwargs={"lazy": "selectin"},
    )
    # lazy="raise": deliveries are high-volume and must never be bulk-traversed through the ORM —
    # the sender queries the table directly. An unsanctioned access raises instead of emitting a
    # ruinous SELECT (which the async engine would surface as MissingGreenlet in production only).
    deliveries: list[BroadcastDelivery] = Relationship(
        cascade_delete=True,
        sa_relationship_kwargs={"lazy": "raise"},
    )

    def __hash__(self) -> int:
        return hash(self.model_dump_json(exclude={"created_time", "updated_time", "id"}))

    def __eq__(self, other: object) -> bool:
        return hash(self) == hash(other) if isinstance(other, Broadcast) else NotImplemented


class BroadcastMessage(BaseModel, SQLModel, table=True):
    __tablename__: str = "broadcast_messages"
    __table_args__ = (UniqueConstraint("broadcast_id", "language", name="uq_broadcast_messages_broadcast_id_language"),)

    id: int | None = Field(default=None, primary_key=True)
    broadcast_id: int | None = Field(default=None, foreign_key="broadcasts.id", ondelete="CASCADE")
    # A value from `mitup_bot.translations.SUPPORTED_LANGUAGES`.
    language: str
    body_html: str
    sent_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    orphan_count: int = 0
    created_time: dt.datetime | None = Field(default=None, sa_column=Column(DateTime, server_default=FetchedValue()))
    updated_time: dt.datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, server_default=FetchedValue(), server_onupdate=FetchedValue()),
    )

    broadcast: Broadcast = Relationship(back_populates="messages", sa_relationship_kwargs={"lazy": "selectin"})

    def __hash__(self) -> int:
        return hash(self.model_dump_json(exclude={"created_time", "updated_time", "id"}))

    def __eq__(self, other: object) -> bool:
        return hash(self) == hash(other) if isinstance(other, BroadcastMessage) else NotImplemented


class BroadcastDelivery(BaseModel, SQLModel, table=True):
    __tablename__: str = "broadcast_deliveries"
    # Uniqueness on (broadcast_id, user_id) is the anti-double-send guarantee: a resumed run cannot
    # insert a second delivery row for a recipient it already enqueued.
    __table_args__ = (UniqueConstraint("broadcast_id", "user_id", name="uq_broadcast_deliveries_broadcast_id_user_id"),)

    id: int | None = Field(default=None, primary_key=True)
    broadcast_id: int | None = Field(default=None, foreign_key="broadcasts.id", ondelete="CASCADE")
    user_id: int | None = Field(default=None, foreign_key="users.id", ondelete="CASCADE")
    language_sent: str
    status: BroadcastDeliveryStatus = Field(
        default=BroadcastDeliveryStatus.PENDING,
        sa_column=Column(
            Enum(
                BroadcastDeliveryStatus,
                native_enum=False,
                length=32,
                values_callable=lambda enum: [m.value for m in enum],
            ),
            nullable=False,
            server_default=BroadcastDeliveryStatus.PENDING.value,
        ),
    )
    sent_time: dt.datetime | None = None

    def __hash__(self) -> int:
        return hash(self.model_dump_json(exclude={"id"}))

    def __eq__(self, other: object) -> bool:
        return hash(self) == hash(other) if isinstance(other, BroadcastDelivery) else NotImplemented
