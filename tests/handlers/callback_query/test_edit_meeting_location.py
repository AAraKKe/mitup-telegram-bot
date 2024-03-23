from typing import cast

from mitup_bot.handlers.edit_meeting.views import edit_location_view
from mitup_bot.models import Meetup
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingMessages
from mitup_bot.views import ButtonConfig, MitupView


def test_edit_location_view(meeting: Meetup):
    meeting_id = cast(int, meeting.id)

    result = edit_location_view(meeting=meeting)
    expected_view = MitupView(
        description=MeetingMessages.EDIT_MEETING_LOCATION.get(),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.MEETING_LOCATION_NAME.get(),
                    callback_data=cb.EDIT_MEETING_LOCATION_NAME.with_id(meeting_id),
                ),
                ButtonConfig(
                    text=ButtonMessages.MEETING_LOCATION_COORDINATES.get(),
                    callback_data=cb.EDIT_MEETING_LOCATION_COORDINATES.with_id(meeting_id),
                ),
            ],
            [
                ButtonConfig(text=ButtonMessages.BACK_EDIT.get(), callback_data=cb.EDIT_MEETING.with_id(meeting_id)),
            ],
        ],
    )

    assert expected_view == result
