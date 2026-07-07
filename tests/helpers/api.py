from dataclasses import dataclass
from typing import Any
from unittest import mock

from telegram import Update

from mitup_bot.api_wrapper import ApiOutbox, TelegramApi
from mitup_bot.models import Meetup, Message, User
from mitup_bot.utils.entities import FormattedText
from mitup_bot.views import InlineResultsButton, MitupInlineView, MitupView
from tests.assertions import assert_awaited_once_with_diff, assert_awaited_with_diff

from .constants import DEFAULT_CURRENT_MESSAGE, DEFAULT_FALSE, DEFAULT_NONE, DefaultValue


@dataclass
class MockApi(TelegramApi):
    """
    Drop-in replacement for the TelegramApi class used for testing. It overrides all methods with mocks
    and provides helper methods to assert whenter the different api methods were called or not.
    """

    def __init__(self):
        self.mock_mapping: dict[str, mock.AsyncMock] = {}

    def begin_capture(self) -> ApiOutbox:
        """MockApi is transparent to write-mode capture: the decorator gets an outbox that
        stays empty, so mocked calls record at call time exactly as under plain handlers and
        the hundreds of existing assertions keep their semantics. Tests that need to observe
        real enqueue/drain ordering should exercise a real TelegramApi instead."""
        return ApiOutbox()

    def send_message_to_user(self, user: User, view: MitupView | FormattedText | str):
        return self.call_mock("send_message_to_user", user=user, view=view)

    def send_message(self, update: Update, view: MitupView | FormattedText | str):
        return self.call_mock("send_message", update=update, view=view)

    def edit_message(self, update: Update, view: MitupView | FormattedText | str):
        return self.call_mock("edit_message", update=update, view=view)

    def edit_message_for_user(self, user: User, message_id: int, view: MitupView | FormattedText | str):
        return self.call_mock("edit_message_for_user", user=user, message_id=message_id, view=view)

    def update_meeting_messages(
        self,
        *,
        meeting: Meetup,
        current_message: Message | None = DEFAULT_NONE,  # type: ignore
        skip_current: bool = DEFAULT_FALSE,  # type: ignore
        was_deleted: bool = DEFAULT_FALSE,  # type: ignore
        has_finished: bool = DEFAULT_FALSE,  # type: ignore
    ):
        return self.call_mock(
            "update_meeting_messages",
            meeting=meeting,
            current_message=current_message,
            skip_current=skip_current,
            was_deleted=was_deleted,
            has_finished=has_finished,
        )

    def answer_callback_query(
        self,
        update: Update,
        text: str | FormattedText | None = DEFAULT_NONE,  # type: ignore
        show_alert: bool = DEFAULT_FALSE,  # type: ignore
    ):
        return self.call_mock("answer_callback_query", update=update, text=text, show_alert=show_alert)

    def answer_inline_query(
        self,
        update: Update,
        results: list[MitupInlineView],
        button: InlineResultsButton | None = DEFAULT_NONE,  # type: ignore
        cache_time: int = DEFAULT_NONE,  # type: ignore
    ):
        return self.call_mock(
            "answer_inline_query", update=update, results=results, button=button, cache_time=cache_time
        )

    async def clear_reply_markup(self, update: Update):
        return await self.call_mock("clear_reply_markup", update=update)

    def call_mock(self, name: str, **kwargs: DefaultValue | Any) -> mock.AsyncMock:
        new_kwargs = {arg: value for arg, value in kwargs.items() if not isinstance(value, DefaultValue)}
        return self.mock_method(name)(**new_kwargs)

    def mock_method(self, name: str) -> mock.AsyncMock:
        return self.mock_mapping.setdefault(name, mock.AsyncMock(name=name))

    def call_args_list(self, name: str) -> mock._CallList:
        return self.mock_method(name).call_args_list

    def call_args(self, name: str) -> mock._Call:
        return self.mock_method(name).call_args

    def assert_method_just_called(self, name: str, times: int = 1):
        """Asserts that a given method has been called, no matter the arguments"""
        mocked_method = self.mock_mapping.get(name)
        if times == 0:
            assert mocked_method is None
        else:
            assert mocked_method is not None
            assert mocked_method.call_count == times

    def assert_send_message_called(self, update: Update, view: MitupView | FormattedText | str, times: int = 1):
        self.assert_method_called("send_message", update=update, view=view, times=times)

    def assert_send_message_to_user_called(self, user: User, view: MitupView | FormattedText | str, times: int = 1):
        if times == 1:
            assert_awaited_once_with_diff(self.mock_method("send_message_to_user"), user=user, view=view)
        else:
            assert_awaited_with_diff(self.mock_method("send_message_to_user"), times, user=user, view=view)

    def assert_edit_message_called(self, update: Update, view: MitupView | FormattedText | str, times: int = 1):
        self.assert_method_called("edit_message", update=update, view=view, times=times)

    def assert_edit_message_for_user_called(
        self, user: User, message_id: int, view: MitupView | FormattedText | str, times: int = 1
    ):
        if times == 1:
            assert_awaited_once_with_diff(
                self.mock_method("edit_message_for_user"), user=user, message_id=message_id, view=view
            )
        else:
            assert_awaited_with_diff(
                self.mock_method("edit_message_for_user"), times, user=user, message_id=message_id, view=view
            )

    def assert_answer_callback_query_called(
        self,
        update: Update,
        text: str | FormattedText | None = None,
        show_alert: bool = False,
        times: int = 1,
    ):
        if times == 1:
            assert_awaited_once_with_diff(
                self.mock_method("answer_callback_query"), update=update, text=text, show_alert=show_alert
            )
        else:
            assert_awaited_with_diff(
                self.mock_method("answer_callback_query"), times, update=update, text=text, show_alert=show_alert
            )

    def assert_answer_inline_query_called(
        self,
        update: Update,
        results: list[MitupInlineView],
        button: object | None = None,
        cache_time: int | None = None,
        times: int = 1,
    ):
        kwargs: dict[str, object] = {"update": update, "results": results}
        if button is not None:
            kwargs["button"] = button
        if cache_time is not None:
            kwargs["cache_time"] = cache_time
        if times == 1:
            assert_awaited_once_with_diff(self.mock_method("answer_inline_query"), **kwargs)
        else:
            assert_awaited_with_diff(self.mock_method("answer_inline_query"), times, **kwargs)

    def assert_update_meeting_messages_called(
        self,
        meeting: Meetup,
        current_message: Message | None = DEFAULT_CURRENT_MESSAGE,
        skip_current: bool | None = None,
        was_deleted: bool | None = None,
        times: int = 1,
    ):
        arguments: dict[str, Any] = {
            "meeting": meeting,
        }
        if current_message != DEFAULT_CURRENT_MESSAGE:
            arguments["current_message"] = current_message
        if skip_current is not None:
            arguments["skip_current"] = skip_current
        if was_deleted is not None:
            arguments["was_deleted"] = was_deleted

        if times == 1:
            assert_awaited_once_with_diff(self.mock_method("update_meeting_messages"), **arguments)
        else:
            assert_awaited_with_diff(self.mock_method("update_meeting_messages"), times, **arguments)

    def assert_update_meeting_messages_not_called(self):
        self.assert_method_just_called("update_meeting_messages", times=0)

    def assert_send_message_not_called(self):
        self.mock_method("send_message").assert_not_called()

    def assert_edit_message_not_called(self):
        self.mock_method("edit_message").assert_not_called()

    def assert_method_called(
        self,
        method_name: str,
        update: Update,
        view: MitupView | FormattedText | str,
        times: int,
    ):
        # Validate that the update has been properly generated.
        # Bot-chat updates have effective_chat + effective_message.
        # Inline message updates have callback_query.inline_message_id instead.
        is_bot_chat = update.effective_chat is not None and update.effective_message is not None
        is_inline = update.callback_query is not None and update.callback_query.inline_message_id is not None
        assert is_bot_chat or is_inline, (
            "Update must have either (effective_chat + effective_message) or "
            "(callback_query.inline_message_id) to call API methods"
        )

        if times == 1:
            assert_awaited_once_with_diff(self.mock_method(method_name), update=update, view=view)
        else:
            assert_awaited_with_diff(self.mock_method(method_name), times, update=update, view=view)
