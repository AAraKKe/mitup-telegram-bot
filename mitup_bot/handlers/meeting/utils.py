from typing import assert_never

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel

from mitup_bot.callback_data import MeetingListSource
from mitup_bot.models import Meetup
from mitup_bot.models.joined_users import JOINED_USERS_UNIQUE_CONSTRAINT
from mitup_bot.utils import ButtonMessages
from mitup_bot.utils import callbacks as cb
from mitup_bot.views import ButtonConfig


def flush_new_participant(session: Session, meeting: Meetup, *new_rows: SQLModel) -> bool:
    """Persist freshly built membership rows inside a savepoint, closing the join race.

    The caller must not add ``new_rows`` beforehand: the helper expunges them and re-adds inside a
    ``begin_nested`` savepoint so a ``joined_users`` uniqueness clash rolls back without poisoning the
    outer transaction, refreshing the meeting's and joining users' ``joined_links`` to the committed
    state. Only that constraint is swallowed — any other IntegrityError re-raises — and the return is
    ``True`` when the rows were inserted, ``False`` when the uniqueness constraint rejected them.
    """
    for row in new_rows:
        if row in session:
            session.expunge(row)
    # Expunge detaches the rows but leaves them lingering in the meeting's and joining users'
    # joined_links collections (they backref in via meetup=/user=). Expire those collections before the
    # flush so it doesn't trip over a detached member, and so a rolled-back clash reloads the committed
    # state (dropping the phantom, picking up a concurrent insert) rather than the stale in-memory list
    # the join fast path reads. Skip users detached by the expunge above (the invite path's new user).
    session.expire(meeting, ["joined_links"])
    for row in new_rows:
        participant = getattr(row, "user", None)
        if participant is not None and participant in session:
            session.expire(participant, ["joined_links"])
    try:
        with session.begin_nested():
            session.add_all(new_rows)
            session.flush()
    except IntegrityError as exc:
        diag = getattr(exc.orig, "diag", None)
        if diag is not None and diag.constraint_name == JOINED_USERS_UNIQUE_CONSTRAINT:
            return False
        raise
    return True


def meeting_detail_back_button(source: MeetingListSource | None, page: int, lang: str) -> ButtonConfig:
    """Back button for a meeting detail screen, targeting the list page the user came from.

    Falls back to the main menu when the originating list is unknown (e.g. reaching the detail
    from an edit flow rather than a list).
    """
    match source:
        case MeetingListSource.ACTIVE:
            return ButtonConfig(
                text=ButtonMessages.ACTIVE_MEETINGS.back(lang=lang),
                callback_data=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(page),
            )
        case MeetingListSource.JOINED:
            return ButtonConfig(
                text=ButtonMessages.JOINED_MEETINGS.back(lang=lang),
                callback_data=cb.SHOW_JOINED_MEETINGS_PAGE.with_id(page),
            )
        case None:
            return ButtonConfig(
                text=ButtonMessages.MAIN_MENU.back(lang=lang),
                callback_data=cb.MAIN_MENU,
            )
        case _ as unreachable:
            assert_never(unreachable)


def meeting_list_button(source: MeetingListSource | None, page: int, lang: str) -> ButtonConfig:
    """Button pointing at the originating list page, labelled after the list itself.

    Used by the meeting-inaccessible fallback view, where the button is an offer to browse the
    list rather than a back action. An unknown origin defaults to the active list.
    """
    if source is MeetingListSource.JOINED:
        return ButtonConfig(
            text=ButtonMessages.JOINED_MEETINGS.get(lang=lang),
            callback_data=cb.SHOW_JOINED_MEETINGS_PAGE.with_id(page),
        )
    return ButtonConfig(
        text=ButtonMessages.ACTIVE_MEETINGS.get(lang=lang),
        callback_data=cb.SHOW_ACTIVE_MEETING_PAGE.with_id(page),
    )
