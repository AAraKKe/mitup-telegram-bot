from unittest.mock import AsyncMock

from telegram.ext import ExtBot


class StubBot(ExtBot, AsyncMock):
    """
    Stubs the ExtBot class for testing purposes.

    It allows to have a bot that is also a mock so we can assert on the calls made to it
    and at the same time satisfy the type checking.

    How it works:
    -------------
    This class inherits from both `ExtBot` (to satisfy type checkers expecting a bot instance)
    and `AsyncMock` (to provide mock capabilities like `assert_called_with`).

    We need to manually override every method we use from `ExtBot` in two places:
    1. **Class Level**: We declare the method as an `AsyncMock` type with a `# type: ignore` comment.
       This overrides the original method signature from `ExtBot`, telling static type checkers
       (like Pylance/MyPy) to treat it as a mock object. This enables autocompletion for assertion
       methods.
    2. **__init__**: We assign a fresh `AsyncMock()` instance to the method name on `self`.
       This ensures that each new instance of `StubBot` (and thus each test) gets its own isolated
       mock object. If we relied only on the class-level attribute, the mock would be shared across
       tests, leading to state leakage.

    **NOTE**: If you use a new method from `context.bot` in your code, you MUST add it here
    following the pattern above.
    """

    # We need to override the methods we use in the api wrapper to be async mocks
    # IMPORTANT: If you add a method here, you MUST also add it to __init__ below!
    send_message: AsyncMock
    edit_message_text: AsyncMock
    answer_inline_query: AsyncMock
    answer_callback_query: AsyncMock
    edit_message_reply_markup: AsyncMock

    def __init__(self, *args, **kwargs):
        # We do not want to initialize the ExtBot as it requires a token
        AsyncMock.__init__(self, *args, **kwargs)

        # Override every method of the bot that we use and ignore type checking because
        # we are overriding them with a mock.
        # This ensures that we have a fresh mock for every test.
        self.send_message = AsyncMock()
        self.edit_message_text = AsyncMock()
        self.answer_inline_query = AsyncMock()
        self.answer_callback_query = AsyncMock()
        self.edit_message_reply_markup = AsyncMock()
