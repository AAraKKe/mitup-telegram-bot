from __future__ import annotations

from typing import TYPE_CHECKING

from mitup_bot.callback_data import CallbackData
from mitup_bot.utils import ButtonMessages, MeetingLifecycleMessages
from mitup_bot.utils.rich_message import horizontal_rule_content
from mitup_bot.views.factory import confirmation_view
from mitup_bot.views.meeting.banner import meeting_photos
from mitup_bot.views.meeting.shared_card import fitted_shared_body
from mitup_bot.views.mitup_view import MitupView

if TYPE_CHECKING:
    from mitup_bot.models import Meetup
    from mitup_bot.views.context import RenderContext


def delete_prompt_view(
    ctx: RenderContext,
    meeting: Meetup,
    *,
    confirm_callback_data: CallbackData,
    decline_callback_data: CallbackData,
    finished: bool = False,
) -> MitupView:
    """The meeting about to go, closed by the line saying it goes for good and the two keys deciding it."""
    body = (
        fitted_shared_body(meeting, finished=finished)
        .append(horizontal_rule_content())
        .append(MeetingLifecycleMessages.DELETE_CONFIRMATION.rich(lang=ctx.lang))
    )
    return confirmation_view(
        ctx,
        message=body,
        confirm_callback_data=confirm_callback_data,
        decline_callback_data=decline_callback_data,
        confirm_label=ButtonMessages.CONFIRM_DELETE_MEETING,
        decline_label=ButtonMessages.DECLINE_DELETE_MEETING,
        confirm_style="danger",
        decline_style=None,
        photos=meeting_photos(meeting),
    )
