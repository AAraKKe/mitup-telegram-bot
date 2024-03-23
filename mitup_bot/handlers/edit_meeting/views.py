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
