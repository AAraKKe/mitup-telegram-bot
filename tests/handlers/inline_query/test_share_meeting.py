import pytest
from telegram import Update
from telegram.ext import Application

from mitup_bot.handlers.inline_query.enums import InlineQueryId
from mitup_bot.models import User
from tests.helpers.context import call_handler
from tests.helpers.fixtures import UpdateRequest, create_meetup, create_user
from tests.helpers.stub_db import MockDbSession


@pytest.mark.parametrize(
    "update, meeting_id, is_owner, is_public, active, should_share",
    [
        (UpdateRequest(inline_query="123"), 123, True, False, True, True),
        (UpdateRequest(inline_query="456"), 456, False, True, True, True),
        (UpdateRequest(inline_query="789"), 789, False, False, True, False),
        (UpdateRequest(inline_query="321"), 321, True, False, False, False),
        (UpdateRequest(inline_query="654"), 654, False, True, False, False),
    ],
    indirect=["update"],
    ids=["owned_meeting", "public_non_owned", "private_not_owned", "inactive_owned", "inactive_public"],
)
async def test_share_meeting(
    update: Update,
    user_with_settings: User,
    mock_session: MockDbSession,
    app: Application,
    meeting_id: int,
    is_owner: bool,
    is_public: bool,
    active: bool,
    should_share: bool,
):
    user = user_with_settings
    mock_session.add_user(user)

    owner = user if is_owner else create_user(id=999, tg_user_id=999, first_name="Owner")
    if not is_owner:
        mock_session.add_user(owner)

    meetup = create_meetup(meeting_id, "Meeting Title", owner=owner, public=is_public, active=active)
    mock_session.add_object(meetup)
    mock_session.commit()

    context, _ = await call_handler(InlineQueryId.SHARE_MEETING, update=update, app=app)

    if should_share:
        context.api.assert_method_just_called("answer_inline_query")
        args, kwargs = context.api.call_args("answer_inline_query")
        results = kwargs.get("results")
        assert results is not None
        assert len(results) == 1
        assert results[0].id == str(meeting_id)
    else:
        context.api.assert_method_just_called("answer_inline_query", times=0)
