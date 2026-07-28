from enum import StrEnum, auto

import structlog

from mitup_bot.models import User

log = structlog.get_logger(__name__)


class MeetingList(StrEnum):
    """Which of the three list screens a line describes, as the `list` facet."""

    ACTIVE = auto()
    JOINED = auto()
    PAST = auto()


def log_meeting_list(
    user: User,
    meeting_list: MeetingList,
    *,
    total: int,
    listed: int,
    requested_page: int,
    page: int,
    **dropped: int,
) -> None:
    """Record what a list screen ended up showing, and what it dropped on the way.

    "My meeting is not in my list" has no other evidence: the counts separate a genuinely empty
    list from one the filters emptied, and a clamp is only reported when it moved the user off the
    page they asked for.
    """
    if page != requested_page:
        log.warning(
            "Meeting list page clamped",
            user_id=user.db_id,
            list=meeting_list.value,
            requested_page=requested_page,
            page=page,
            item_count=listed,
            reason="page_out_of_range",
        )

    if not listed:
        log.info(
            "Meeting list empty",
            user_id=user.db_id,
            list=meeting_list.value,
            total=total,
            reason="no_matching_meetings",
            **dropped,
        )
        return

    log.info(
        "Meeting list built",
        user_id=user.db_id,
        list=meeting_list.value,
        total=total,
        listed=listed,
        requested_page=requested_page,
        page=page,
        **dropped,
    )
