from mitup_bot.models import Meetup
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingMessages
from mitup_bot.views import ButtonConfig, MitupView, factory


def edit_location_view(meeting: Meetup) -> MitupView:
    assert meeting.id is not None
    extra_options = [
        [
            ButtonConfig(
                text=ButtonMessages.MEETING_LOCATION_NAME.get(),
                callback_data=cb.EDIT_MEETING_LOCATION_NAME.with_id(meeting.id),
            ),
            ButtonConfig(
                text=ButtonMessages.MEETING_LOCATION_COORDINATES.get(),
                callback_data=cb.EDIT_MEETING_LOCATION_COORDINATES.with_id(meeting.id),
            ),
        ]
    ]

    return factory.edit_meeting_property_view(
        MeetingMessages.EDIT_MEETING_LOCATION.get(), meeting.id, extra_buttons=extra_options
    )


def edit_participants_view(meeting_id: int) -> MitupView:
    extra_options = [
        [
            ButtonConfig(
                text=ButtonMessages.MEETING_MAX_PARTICIPANTS.get(),
                callback_data=cb.EDIT_MEETING_MAX_PARTICIPANTS.with_id(meeting_id),
            ),
            ButtonConfig(
                text=ButtonMessages.MEETING_KICK_OUT.get(),
                callback_data=cb.EDIT_MEETING_KICK_OUT_PARTICIPANS.with_id(meeting_id),
            ),
        ]
    ]

    return factory.edit_meeting_property_view(
        MeetingMessages.EDIT_MEETING_PARTICIPANTS.get(),
        meeting_id,
        extra_buttons=extra_options,
    )


def edit_max_participants_view(meeting_id: int, fail: bool = False) -> MitupView:
    return MitupView(
        description=(
            MeetingMessages.MAX_PARTICIPANTS_SET_FAIL.get()
            if fail
            else MeetingMessages.EDIT_MEETING_MAX_PARTICIPANTS.get()
        ),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.MEETING_NO_LIMIT_PARTICIPANTS.get(),
                    callback_data=cb.EDIT_MEETING_NO_LIMIT_PARTICIPANTS.with_id(meeting_id),
                ),
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get(),
                    callback_data=cb.CANCEL_EDIT_MEETING_PARTICIPANS.with_id(meeting_id),
                ),
            ]
        ],
    )
