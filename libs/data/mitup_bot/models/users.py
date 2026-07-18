import datetime as dt
from collections.abc import Sequence
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, Self, cast, overload

from sqlalchemy import BigInteger, Column, DateTime, Enum, FetchedValue
from sqlalchemy.orm import QueryableAttribute, selectinload
from sqlalchemy.orm.interfaces import LoaderOption
from sqlmodel import Field, Relationship, SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from mitup_bot import supporter
from mitup_bot.exceptions import UserNotFound
from mitup_bot.supporter import SupporterLevel

from . import JoinedUsers, Meetup
from .base_model import BaseModel

if TYPE_CHECKING:
    from . import JoinedUsers, Meetup, Settings


class UserStatus(StrEnum):
    """Lifecycle state of a `User` row.

    MEMBER users have engaged via DM and are reachable; JOINED_ONLY users joined a
    meeting via inline button and are not reachable until they `/start`; LEFT users
    were MEMBERs who blocked or deleted the bot; DELETION_REQUESTED users asked for
    their data to be wiped and every interaction is rejected until the cleanup run
    purges the row.
    """

    MEMBER = "member"
    JOINED_ONLY = "joined_only"
    LEFT = "left"
    DELETION_REQUESTED = "deletion_requested"


class User(BaseModel, SQLModel, table=True):
    # Until better configuration is available through SQLModel (https://github.com/tiangolo/sqlmodel/issues/159)
    __tablename__: str = "users"

    first_name: str
    tg_user_id: int = Field(sa_type=BigInteger)
    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger)
    created_time: dt.datetime | None = Field(default=None, sa_column=Column(DateTime, server_default=FetchedValue()))
    updated_time: dt.datetime | None = Field(
        default=None,
        sa_column=Column(DateTime, server_default=FetchedValue(), server_onupdate=FetchedValue()),
    )
    # native_enum=False keeps the column a plain VARCHAR(32) while coercing loaded rows back to
    # UserStatus: a bare String column returns plain strs, silently failing `status is UserStatus.X`
    # checks everywhere a User is loaded from the database.
    status: UserStatus = Field(
        default=UserStatus.MEMBER,
        sa_column=Column(
            Enum(UserStatus, native_enum=False, length=32, values_callable=lambda enum: [m.value for m in enum]),
            nullable=False,
            server_default=UserStatus.MEMBER.value,
        ),
    )
    last_name: str | None = None
    username: str | None = None
    # Kept directly on User (rather than joined from supporter_subscriptions) so every handler that
    # gates on support status reads it without a join; the recurring job and OAuth callback keep it
    # in sync with the subscription row. native_enum=False keeps the column a plain VARCHAR (no DB
    # enum type) while coercing loaded rows back to SupporterLevel.
    supporter_level: SupporterLevel = Field(
        default=SupporterLevel.NONE,
        sa_column=Column(
            Enum(
                SupporterLevel,
                native_enum=False,
                length=16,
                values_callable=lambda enum: [member.value for member in enum],
            ),
            nullable=False,
            server_default=SupporterLevel.NONE.value,
        ),
    )
    # lazy="selectin": `user.lang` is read for virtually every loaded user (including meeting
    # participants), and implicit lazy loads raise MissingGreenlet under the async engine.
    settings: Settings = Relationship(
        back_populates="user",
        cascade_delete=True,
        sa_relationship_kwargs={"uselist": False, "lazy": "selectin"},
    )
    # meetups and joined_links deliberately don't eager-load: doing so on every User would
    # recurse through Meetup.joined_links -> JoinedUsers.user -> User.meetups across the whole
    # social graph. They are only traversed on the *current* user, so `by_tg_user_id` loads them
    # explicitly via chained selectinload options instead (freshly flushed users use session.refresh).
    # lazy="raise": an unsanctioned unloaded access would otherwise emit a lazy SELECT, which
    # the async engine turns into a MissingGreenlet in production only. Raising a clear
    # InvalidRequestError instead makes the bad access deterministic and unit-test-visible.
    meetups: list[Meetup] = Relationship(
        back_populates="owner",
        cascade_delete=True,
        sa_relationship_kwargs={"lazy": "raise"},
    )
    joined_links: list[JoinedUsers] = Relationship(
        back_populates="user",
        cascade_delete=True,
        sa_relationship_kwargs={"foreign_keys": "JoinedUsers.user_id", "lazy": "raise"},
    )

    def __hash__(self) -> int:
        return hash(self.model_dump_json(exclude={"created_time", "updated_time", "id"}))

    def __eq__(self, other: object) -> bool:
        return hash(self) == hash(other) if isinstance(other, User) else NotImplemented

    @overload
    @classmethod
    async def by_tg_user_id(
        cls,
        session: AsyncSession,
        tg_user_id: int,
        must_exist: Literal[True],
        *,
        load_collections: bool = ...,
        load_participants: bool = ...,
    ) -> Self: ...

    @overload
    @classmethod
    async def by_tg_user_id(
        cls,
        session: AsyncSession,
        tg_user_id: int,
        must_exist: bool = ...,
        *,
        load_collections: bool = ...,
        load_participants: bool = ...,
    ) -> Self | None: ...

    @classmethod
    async def by_tg_user_id(
        cls,
        session: AsyncSession,
        tg_user_id: int,
        must_exist: bool = False,
        *,
        load_collections: bool = True,
        load_participants: bool = False,
    ) -> Self | None:
        # The default loads the one-hop `meetups`/`joined_links` (plus `joined_links -> meetup`) that
        # handlers and list views traverse. `load_participants=True` additionally spells out each
        # meeting's `owner` and participant leaves; pass it only from handlers that render a full
        # meeting card straight off these collections (the inline query), since selectin does not
        # cascade through the user-rooted load cycle — see `user_collection_loaders` and the database
        # skill. `load_collections=False` skips the collections entirely (they are `lazy="raise"`, so
        # such an instance must not touch either).
        if load_participants and not load_collections:
            raise ValueError("load_participants=True requires load_collections=True")

        statement = select(cls).where(cls.tg_user_id == tg_user_id)
        if load_collections:
            statement = statement.options(*user_collection_loaders(participants=load_participants))
        if (found_user := (await session.exec(statement)).first()) is not None:
            return found_user

        if must_exist:
            raise UserNotFound(tg_user_id)

        return None

    @property
    def inline_name(self) -> str:
        """
        Name to use for the user in inline messages.

        If the user has a username, use that, otherwise fall back to first name.
        """
        return self.username or self.first_name

    @property
    def display_name(self) -> str:
        """`inline_name` with the supporter badge for the user's tier prepended (none for NONE).

        The per-tier badge is resolved through the supporter policy, so the emoji-per-level mapping
        lives in one place. The badge rides existing name displays only: callers that hide a name
        (e.g. incognito meetings omit the participant list entirely) never reach this, so the badge
        inherits the same visibility as the name it decorates and creates no new identity exposure.
        """
        if tier_badge := supporter.badge(self.supporter_level):
            return f"{tier_badge} {self.inline_name}"
        return self.inline_name

    @property
    def lang(self) -> str:
        return self.settings.language

    def mark_inactive(self) -> bool:
        """Transition a MEMBER user to LEFT.

        Returns `True` iff a real transition happened. JOINED_ONLY and LEFT
        users are no-ops so callers (e.g. the `INACTIVE_USER_SET` metric path)
        only react on genuine member departures.
        """
        if self.status is UserStatus.MEMBER:
            self.status = UserStatus.LEFT
            return True
        return False

    def joined_meeting(self, meeting_id: int) -> JoinedUsers | None:
        joined_links = [joined for joined in self.joined_links if joined.meetup_id == meeting_id]
        return joined_links[0] if joined_links else None

    def own_meeting(self, meeting_id: int) -> Meetup | None:
        return next((meetup for meetup in self.meetups if meetup.db_id == meeting_id), None)

    def datetime_in_tz(self, datetime: dt.datetime) -> dt.datetime:
        return datetime.astimezone(self.settings.tz)

    def now_in_tz(self) -> dt.datetime:
        return self.datetime_in_tz(dt.datetime.now(dt.UTC))


def as_loadable(relationship: Any) -> QueryableAttribute[Any]:
    """Cast a SQLModel relationship attribute to the type `selectinload` expects.

    SQLModel types relationship class attributes as their instance values, not as the
    InstrumentedAttribute SQLAlchemy actually puts on the class; loader options need the latter.
    """
    return cast("QueryableAttribute[Any]", relationship)


def user_collection_loaders(*, participants: bool) -> Sequence[LoaderOption]:
    """Loader options for a user-rooted load of `meetups` and `joined_links`.

    Selectin does not cascade through the User -> Meetup -> JoinedUsers -> User load-path cycle, so
    each hop the views read must be named (see the database skill). Without `participants` (the
    default): the one-hop collections plus `joined_links -> meetup` (the list screens read
    `link.meetup.title`/`active`). With `participants`: additionally each meeting's `owner` and its
    participants' `user`/`invited_by`, for the full meeting-card renderers.

    The two roots double as builders for the shared Meetup leaves; SQLAlchemy loader options are
    generative, so the branches off one root do not interfere — pinned by
    `test_load_options_are_generative`.
    """
    meetups_root = selectinload(as_loadable(User.meetups))
    joined_meetup_root = selectinload(as_loadable(User.joined_links)).selectinload(as_loadable(JoinedUsers.meetup))
    options = [meetups_root, joined_meetup_root]
    if participants:
        for meetup_root in (meetups_root, joined_meetup_root):
            joined_links_leaf = meetup_root.selectinload(as_loadable(Meetup.joined_links))
            options += [
                meetup_root.selectinload(as_loadable(Meetup.owner)),
                joined_links_leaf.selectinload(as_loadable(JoinedUsers.user)),
                joined_links_leaf.selectinload(as_loadable(JoinedUsers.invited_by)),
            ]
    return options
