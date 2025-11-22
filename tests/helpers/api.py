from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from unittest import mock

from telegram import Update

from mitup_bot.custom_context import MitupContext
from mitup_bot.models import Meetup, Message, User
from mitup_bot.views import MitupView
from tests.assertions import assert_awaited_once_with_diff, assert_awaited_with_diff
from tests.helpers.stub_db import MockDbSession

from .types import DEFAULT_CURRENT_MESSAGE


@dataclass
class MockApi:
    """
    This is a helper class that helps aserring if we have called api methods via patching those methods and exposing
    easy to use assert methods.

    We do not rely on testing the bot methods called but instead we assert calls on the methods in `mitup_bot.api`. The
    intention is to keep testing at a higher abstraction level working with views instead of having to test the low
    level telegram library methods.

    The api is instantiated from the MockApi.start() method. The module path where the api module is being imported
    needs to be provided to start to be able to start the patching. The method patching is released when out of context.
    """

    send_message_mock: mock.AsyncMock
    send_to_user_mock: mock.AsyncMock
    edit_message_mock: mock.AsyncMock
    update_meeting_messages_mock: mock.AsyncMock
    answer_callback_query_mock: mock.AsyncMock

    @classmethod
    @contextmanager
    def start(cls, module_path: str) -> Generator[MockApi]:
        with (
            mock.patch(f"{module_path}.api.edit_message") as edit_patch,
            mock.patch(f"{module_path}.api.send_message") as send_patch,
            mock.patch(f"{module_path}.api.send_message_to_user") as send__to_user_patch,
            mock.patch(f"{module_path}.api.update_meeting_messages") as update_meeting_messages_patch,
            mock.patch(f"{module_path}.api.answer_callback_query") as answer_callback_query_patch,
        ):
            yield MockApi(
                send_message_mock=send_patch,
                send_to_user_mock=send__to_user_patch,
                edit_message_mock=edit_patch,
                update_meeting_messages_mock=update_meeting_messages_patch,
                answer_callback_query_mock=answer_callback_query_patch,
            )

    def assert_send_message_called(
        self, context: mock.MagicMock | MitupContext, update: Update, view: MitupView | str, times: int = 1
    ):
        self.assert_method_called(self.send_message_mock, context, update, view, times)

    def assert_edit_message_called(
        self, context: mock.MagicMock | MitupContext, update: Update, view: MitupView | str, times: int = 1
    ):
        self.assert_method_called(self.edit_message_mock, context, update, view, times)

    def assert_send_to_user_called(
        self, context: mock.MagicMock | MitupContext, user: User, view: MitupView | str, times: int = 1
    ):
        if times == 1:
            assert_awaited_once_with_diff(self.send_to_user_mock, context_or_bot=context, user=user, view=view)
        else:
            assert_awaited_with_diff(self.send_to_user_mock, times, context_or_bot=context, user=user, view=view)

    def assert_answer_callback_query_called(
        self,
        context: mock.MagicMock | MitupContext,
        update: Update,
        text: str | None = None,
        show_alert: bool = False,
        times: int = 1,
    ):
        if times == 1:
            assert_awaited_once_with_diff(
                self.answer_callback_query_mock, context=context, update=update, text=text, show_alert=show_alert
            )
        else:
            assert_awaited_with_diff(
                self.answer_callback_query_mock, times, context=context, update=update, text=text, show_alert=show_alert
            )

    def assert_update_meeting_messages_called(
        self,
        session: MockDbSession,
        context: mock.MagicMock | MitupContext,
        meeting: Meetup,
        current_message: Message | None = DEFAULT_CURRENT_MESSAGE,
        skip_current: bool = False,
        times: int = 1,
    ):
        arguments = {
            "session": session,
            "context_or_bot": context,
            "meeting": meeting,
        }
        if current_message != DEFAULT_CURRENT_MESSAGE:
            arguments["current_message"] = current_message
        if skip_current:
            arguments["skip_current"] = skip_current

        if times == 1:
            assert_awaited_once_with_diff(self.update_meeting_messages_mock, **arguments)
        else:
            assert_awaited_with_diff(self.update_meeting_messages_mock, times, **arguments)

    def assert_send_message_not_called(self):
        self.send_message_mock.assert_not_called()

    def assert_edit_message_not_called(self):
        self.edit_message_mock.assert_not_called()

    def assert_method_called(
        self,
        method: mock.AsyncMock,
        context: mock.MagicMock | MitupContext,
        update: Update,
        view: MitupView | str,
        times: int,
    ):
        # Validate that the update has been properly generated
        assert update.effective_chat is not None
        assert update.effective_message is not None

        if times == 1:
            assert_awaited_once_with_diff(method, context=context, update=update, view=view)
        else:
            assert_awaited_with_diff(method, times, context=context, update=update, view=view)
