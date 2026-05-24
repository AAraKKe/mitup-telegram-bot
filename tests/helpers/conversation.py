from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from telegram import CallbackQuery, Chat, Message
from telegram import User as TgUser

from mitup_bot.handler_id import HandlerId
from tests.helpers.constants import (
    DEFAULT_CHAT_ID,
    DEFAULT_MESSAGE_ID,
    DEFAULT_TEST_DATE,
    DEFAULT_TG_USER_PARAMS,
)
from tests.helpers.context import call_handler
from tests.helpers.fixtures import UpdateRequest, create_test_app, create_update
from tests.helpers.handler_context import HandlerContext
from tests.helpers.monitoring import make_test_metrics_client
from tests.helpers.types import StubMitupApp, StubMitupContext


@dataclass
class ConversationStep:
    """
    Represents a single step in a conversation for testing purposes.
    Attributes:
        input: The UpdateRequest representing the user action (message or callback).
        expected_state: The expected state of the conversation after this step.
        after: An optional callable to execute after this step.
    """

    input: UpdateRequest
    expected_state: Any | None = None
    after: Callable[[], None] | None = None

    @classmethod
    def message(
        cls,
        text: str,
        expected_state: None | Enum = None,
        after: Callable[[], None] | None = None,
        **kwargs,
    ) -> ConversationStep:
        """
        Create a ConversationStep representing a user message.
        The arguments are the same as for the ConversationStep constructor.

        kwargs: Additional keyword arguments to pass to the UpdateRequest constructor when creating
        the message update.
        """

        return cls(input=UpdateRequest(message_text=text), expected_state=expected_state, after=after, **kwargs)

    @classmethod
    def callback(
        cls,
        data: Any,
        expected_state: None | Enum = None,
        after: Callable[[], None] | None = None,
        **kwargs,
    ) -> ConversationStep:
        """
        Create a ConversationStep representing a user callback query.
        The arguments are the same as for the ConversationStep constructor.

        kwargs: Additional keyword arguments to pass to the UpdateRequest constructor when creating
        the callback query update.
        """
        return cls(input=UpdateRequest(callback_query=data), expected_state=expected_state, after=after, **kwargs)


@dataclass
class StepResult:
    """
    Represents the result of a single step in a conversation test.
    Attributes:
        step_index: The index of the step in the conversation.
        context: The StubMitupContext after processing the step.
        state: The resulting state of the conversation after the step.
        update_request: The UpdateRequest that was processed in this step.
    """

    step_index: int
    context: StubMitupContext
    state: Enum | None
    update_request: UpdateRequest


class ConversationResult:
    def __init__(self, step_results: list[StepResult]):
        self.history = step_results

    @property
    def last_state(self) -> Enum | None:
        return self.history[-1].state if self.history else None

    @property
    def last_context(self) -> StubMitupContext:
        if not self.history:
            raise IndexError("Conversation history is empty")
        return self.history[-1].context

    def get_step(self, index: int) -> StepResult:
        return self.history[index]


class ConversationTester:
    def __init__(self, app: StubMitupApp | None = None, user: TgUser | None = None, chat: Chat | None = None):
        self.app = app or create_test_app()
        self.user = user or TgUser(**DEFAULT_TG_USER_PARAMS)
        self.chat = chat or Chat(id=DEFAULT_CHAT_ID, type="private")

        # Default objects needed for update creation
        self._tg_message = Message(
            message_id=DEFAULT_MESSAGE_ID, date=DEFAULT_TEST_DATE, chat=self.chat, from_user=self.user, text="default"
        )
        self._tg_callback_query = CallbackQuery(
            id=str(DEFAULT_MESSAGE_ID), from_user=self.user, message=self._tg_message, chat_instance="inst"
        )

    async def run(
        self,
        handler_id: HandlerId,
        steps: Sequence[ConversationStep | UpdateRequest],
    ) -> ConversationResult:
        """
        Run a sequence of conversation steps against the specified handler.

        Args:
            handler_id: The ID of the conversation handler to test.
            steps: A list of ConversationStep or UpdateRequest objects representing the user actions.

        Returns:
            A ConversationResult object containing the history of the conversation.
        """
        step_results = []

        for i, step in enumerate(steps):
            req = step.input if isinstance(step, ConversationStep) else step

            update = create_update(req, self.chat, self.user, self._tg_message, self._tg_callback_query)

            handler_ctx = HandlerContext(update=update, app=self.app, metrics_client=make_test_metrics_client())
            context, result = await call_handler(handler_id, handler_context=handler_ctx)

            step_result = StepResult(step_index=i, context=context, state=result, update_request=req)
            step_results.append(step_result)

            if isinstance(step, ConversationStep):
                assert result == step.expected_state, (
                    f"Step {i}: Expected state {step.expected_state}, but got {result}. Input: {req}"
                )

                if step.after:
                    step.after()

        return ConversationResult(step_results)
