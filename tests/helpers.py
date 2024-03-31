from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from unittest import mock

from telegram import Update, CallbackQuery
from telegram.ext import CallbackContext

from mitup_bot.views import MitupView
from mitup_bot.callback_data import CallbackData


@dataclass
class UpdateRequest:
    """
    A data class representing a Telegram update we want injected as a fixture.

    Every type of update managed in the bot will include an user, a chat and a message. Since the most common type of
    update handled by the bot, message defaults to False. Otherwise, the update will be a pure message update.

    Args:
        user (bool, optional): Whether to include user information in the update request. Defaults to True.
        chat (bool, optional): Whether to include chat information in the update request. Defaults to True.
        message (bool, optional): Whether to include message information in the update request. Defaults to True.
        callback_data (CallbackData | bool, optional): Defines whether or not the update should include callback data.
            If True, a default CallbackQuery will be added. If a CallbackData object is provided, it will be used to
            generate the CallbackQuery. Defaults to False.
        inline_query (str, optional): The inline query string. Defaults to "".
    """

    user: bool = True
    chat: bool = True
    message: bool = True
    callback_query: CallbackData | bool = False
    inline_query: str = ""


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
    edit_message_mock: mock.AsyncMock

    @classmethod
    @contextmanager
    def start(cls, module_path: str) -> Generator["MockApi", None, None]:
        with (
            mock.patch(f"{module_path}.api.edit_message") as edit_patch,
            mock.patch(f"{module_path}.api.send_message") as send_patch,
        ):
            yield MockApi(send_message_mock=send_patch, edit_message_mock=edit_patch)

    def assert_send_message_called(
        self, context: mock.MagicMock | CallbackContext, update: Update, view: MitupView | str, times: int = 1
    ):
        self.assert_method_called(self.send_message_mock, context, update, view, times)

    def assert_edit_message_called(
        self, context: mock.MagicMock | CallbackContext, update: Update, view: MitupView | str, times: int = 1
    ):
        self.assert_method_called(self.edit_message_mock, context, update, view, times)

    def assert_method_called(
        self,
        method: mock.AsyncMock,
        context: mock.MagicMock | CallbackContext,
        update: Update,
        view: MitupView | str,
        times: int,
    ):
        # Validate that the update has been properly generated
        assert update.effective_chat is not None
        assert update.effective_message is not None

        if times == 1:
            method.assert_awaited_once_with(context, update, view)
        else:
            # If more than one time we need to assert that we have called it the amount of times requested
            # and at least one of them with the appropriate arguments
            assert len(method.call_args_list) == times, f"Expected {times} call but found {len(method.call_args_list)}"
            expected_call = mock.call(context, update, view)
            assert any(
                expected_call == call for call in method.await_args_list
            ), f"Expected call {expected_call} not found in method"
