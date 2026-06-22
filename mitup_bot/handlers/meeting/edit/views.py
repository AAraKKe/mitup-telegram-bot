from mitup_bot.callback_data import MeetingCallbackData
from mitup_bot.models import Meetup, User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import ButtonMessages, MeetingEditLocationMessages, MeetingEditParticipantsMessages
from mitup_bot.views import ButtonConfig, MitupView, PaginatedMitupView, factory


def edit_location_view(meeting: Meetup) -> MitupView:
    extra_options = [
        [
            ButtonConfig(
                text=ButtonMessages.MEETING_LOCATION_NAME.get(lang=meeting.lang),
                callback_data=cb.EDIT_MEETING_LOCATION_NAME.with_id(meeting.db_id),
            ),
            ButtonConfig(
                text=ButtonMessages.MEETING_LOCATION_COORDINATES.get(lang=meeting.lang),
                callback_data=cb.EDIT_MEETING_LOCATION_COORDINATES.with_id(meeting.db_id),
            ),
        ]
    ]

    return factory.edit_meeting_property_view(
        lang=meeting.lang,
        message=MeetingEditLocationMessages.DESCRIPTION.get(lang=meeting.lang),
        meeting_id=meeting.db_id,
        extra_buttons=extra_options,
    )


def edit_participants_view(meeting: Meetup) -> MitupView:
    buttons = [
        ButtonConfig(
            text=ButtonMessages.MEETING_MAX_PARTICIPANTS.get(lang=meeting.owner.lang),
            callback_data=cb.EDIT_MEETING_MAX_PARTICIPANTS.with_id(meeting.db_id),
        )
    ]

    participants_to_kick_out = [
        participant for participant in meeting.participants if participant.user.db_id != meeting.owner.db_id
    ]
    if participants_to_kick_out:
        buttons.append(
            ButtonConfig(
                text=ButtonMessages.MEETING_KICK_OUT.get(lang=meeting.owner.lang),
                callback_data=cb.EDIT_MEETING_KICK_OUT_PARTICIPANTS.with_ids(meeting_id=meeting.db_id, id=1),
            )
        )

    return factory.edit_meeting_property_view(
        lang=meeting.lang,
        message=MeetingEditParticipantsMessages.DESCRIPTION.get(lang=meeting.owner.lang),
        meeting_id=meeting.db_id,
        extra_buttons=[buttons],
    )


def edit_max_participants_view(meeting: Meetup, fail: bool = False) -> MitupView:
    return MitupView(
        description=(
            MeetingEditParticipantsMessages.MAX_INVALID.get(lang=meeting.lang)
            if fail
            else MeetingEditParticipantsMessages.MAX_PROMPT.get(lang=meeting.lang)
        ),
        keyboard=[
            [
                ButtonConfig(
                    text=ButtonMessages.MEETING_NO_LIMIT_PARTICIPANTS.get(lang=meeting.lang),
                    callback_data=cb.EDIT_MEETING_NO_LIMIT_PARTICIPANTS.with_id(meeting.db_id),
                ),
                ButtonConfig(
                    text=ButtonMessages.CANCEL.get(lang=meeting.lang),
                    callback_data=cb.CANCEL_EDIT_MEETING_PARTICIPANS.with_id(meeting.db_id),
                ),
            ]
        ],
    )


def kick_out_users_view(
    meeting: Meetup,
    current_user: User,
    page_number: int = 1,
) -> PaginatedMitupView:
    """
    Build the view that shows the list of users to kick out as a paginated view on the selected page.
    """
    return PaginatedMitupView(
        description=MeetingEditParticipantsMessages.KICK_OUT_DESCRIPTION.get(lang=current_user.lang),
        buttons=[
            factory.user_button(
                participant.user, cb.EDIT_MEETING_KICK_OUT_ACTION.with_ids(meeting.db_id, participant.user.db_id)
            )
            for participant in meeting.participants
            if participant.user.db_id != current_user.db_id
        ],
        page_number=page_number,
        column_size=2,
        row_size=5,
        # Use the kickout callback using the entity as the page instead of user to maintain meeting id information
        navigation_callback_data=MeetingCallbackData(entity="kickout_page", action="show", meeting_id=meeting.db_id),
    ).with_context_menu(
        [
            [
                ButtonConfig(
                    text=f"{ButtonMessages.EDIT.back(lang=current_user.lang)}",
                    callback_data=cb.EDIT_MEETING.with_id(meeting.db_id),
                )
            ]
        ]
    )
