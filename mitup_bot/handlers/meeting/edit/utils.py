from mitup_bot.custom_context import ContextId
from mitup_bot.utils.mitup_types import TMitupContext


def cleanup_states(context: TMitupContext) -> None:
    context.clean_user_data(
        [
            ContextId.EDIT_MEETING_TITLE,
            ContextId.EDIT_MEETING_DESCRIPTION,
            ContextId.EDIT_MEETING_LOCATION_NAME,
            ContextId.EDIT_MEETING_LOCATION_COORDINATES,
            ContextId.EDIT_MEETING_TIME,
            ContextId.EDIT_MEETING_DURATION,
        ]
    )
