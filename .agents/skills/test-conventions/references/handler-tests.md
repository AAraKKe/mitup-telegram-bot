# Handler Test Patterns

## Executing handlers

Never call handler functions directly. Always use `call_handler` from `tests.helpers.context`:

```python
from tests.helpers import call_handler

context, state = await call_handler(HandlerId, update=update, app=app)
```

It builds the context (with `MockApi` and `StubMetricsEngine`), runs `check_update` to validate the update matches the handler, then executes the handler. Returns `(StubMitupContext, state)`.

### Overloads

```python
# With update + app (most common)
context, state = await call_handler(HandlerId, update=update, app=app)

# With HandlerContext (bundles update + app)
context, state = await call_handler(HandlerId, handler_context=handler_context)

# With pre-populated meeting IDs in context
context, state = await call_handler(
    HandlerId, update=update, app=app,
    with_meeting_id={ContextId.EDIT_MEETING_TIME: 99},
)
```

### Conversation entry points

For conversation handlers, pass the **individual handler ID** (e.g., `CommandsId.START_WITH_EXISTING_USER`), NOT the ConversationHandler ID. The conversation handler stores state under `(chat_id, user_id)` but `call_handler` looks up `(user_id,)` for non-default per_chat settings, so using the wrong ID returns `None` for the state.

## Complete callback handler test

```python
import pytest
from telegram import Update

from mitup_bot.handlers.meeting import MeetingHandlerId
from mitup_bot.models import User
from mitup_bot.utils import callbacks as cb
from mitup_bot.utils.messages import MeetingMessages
from mitup_bot.views import factory
from tests.helpers import MockDbSession, StubMitupApp, UpdateRequest, call_handler


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.DELETE_MEETING.with_id(1))], indirect=True)
async def test_delete_meeting_shows_confirmation(
    mock_session: MockDbSession,
    update: Update,
    user_with_settings: User,
    app: StubMitupApp,
):
    # Seed the mock session
    mock_session.add_object(user_with_settings, "tg_user_id")
    mock_session.add_object(user_with_settings.meetups[0])

    # Execute the handler
    context, _ = await call_handler(MeetingHandlerId.DELETE_MEETING_CALLBACK, update=update, app=app)

    # Assert the response
    context.api.assert_edit_message_called(
        update,
        factory.confirmation_view(
            lang=user_with_settings.lang,
            message=MeetingMessages.DELETE_MEETING.get(lang=user_with_settings.lang),
            confirm_callback_data=cb.CONFIRM_DELETE_MEETING.with_id(1),
            decline_callback_data=cb.DECLINE_DELETE_MEETING.with_id(1),
        ),
    )
    context.api.assert_method_just_called("send_message", times=0)
```

## Conversation flow tests

Use `ConversationTester` for multi-step conversation flows:

```python
from tests.helpers import ConversationTester, ConversationStep

async def test_conversation_flow(mock_session: MockDbSession, conversation: ConversationTester):
    # Setup mock_session as needed...

    result = await conversation.run(
        handler_id=SomeConversationHandlerId.ENTRY,
        steps=[
            ConversationStep.callback(
                data=cb.SOME_CALLBACK,
                expected_state=SomeState.WAITING_INPUT,
            ),
            ConversationStep.message(
                text="User input",
                expected_state=SomeState.DONE,
            ),
        ],
    )

    # Assert on the final context
    result.last_context.api.assert_edit_message_called(...)
```

### ConversationStep helpers

| Factory | Purpose |
|---|---|
| `ConversationStep.message(text, expected_state=None, after=None)` | Simulates a text message |
| `ConversationStep.callback(data, expected_state=None, after=None)` | Simulates a callback button press |

The `after` callable runs after the step — useful for mutating mock_session state between steps.

The `expected_state` is asserted automatically. Pass `None` to skip state assertion.

### Accessing step results

```python
result.last_state          # State after the last step
result.last_context        # Context from the last step
result.get_step(0).context # Context from a specific step
result.history             # All StepResult objects
```

## Inline message tests

For tests involving shared/inline messages (not from the bot's private chat):

```python
UpdateRequest(from_bot_chat=False)  # Uses inline_message_id instead of effective_chat
```

Defaults: `chat_instance="someinstance"`, `inline_message_id="some_inline_message_id"`.
