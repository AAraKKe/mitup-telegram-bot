from mitup_bot.keyboards import ButtonConfig
from mitup_bot.models import Meetup, User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingEditParticipantsMessages
from mitup_bot.utils.rich_message import RichContent
from mitup_bot.utils.rich_template import render_rich
from mitup_bot.views import MitupView
from mitup_bot.views.meeting_text import participant_name


def edit_max_participants_view(meeting: Meetup, fail: bool = False) -> MitupView:
    # Removing an existing limit lives on the editor card as its own control, so this prompt only
    # asks for the number.
    description = (
        MeetingEditParticipantsMessages.MAX_INVALID.rich(lang=meeting.lang)
        if fail
        else MeetingEditParticipantsMessages.LIMIT_PROMPT.rich(lang=meeting.lang)
    )
    return MitupView(
        message=description,
        menu=[
            [
                ButtonConfig(
                    text=ButtonMessages.CANCEL.text(lang=meeting.lang),
                    callback_data=cb.CANCEL_EDIT_MEETING_PARTICIPANS.with_id(meeting.db_id),
                ),
            ]
        ],
    )


def kick_out_users_view(meeting: Meetup, current_user: User) -> MitupView:
    """The kick-out list: every participant except the owner on their own line, a kick chip
    beside the name. The whole list fits one message because a long rich body folds behind the
    client's "Show more" control instead of needing pages."""
    lines = []
    for participant in meeting.participants:
        if participant.user.db_id == current_user.db_id:
            continue
        name = participant_name(participant)
        chip = ButtonConfig(
            text=ButtonMessages.REMOVE.text(lang=current_user.lang),
            callback_data=cb.EDIT_MEETING_KICK_OUT_ACTION.with_ids(meeting.db_id, participant.user.db_id),
            style="danger",
        )
        lines.append(render_rich(t"{name} {chip}"))
    body = (
        MeetingEditParticipantsMessages.KICK_OUT_DESCRIPTION.rich(lang=current_user.lang)
        .append("\n\n")
        .append(RichContent.join("\n", lines))
    )
    return MitupView(body).with_back_button(
        ButtonMessages.MEETING, current_user.lang, cb.EDIT_MEETING.with_id(meeting.db_id)
    )
