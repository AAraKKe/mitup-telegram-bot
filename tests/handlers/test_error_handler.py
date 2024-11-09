from typing import cast

import pytest

from mitup_bot.config import Env
from mitup_bot.exceptions import InactiveUserInteraction
from mitup_bot.handlers import error_handler
from mitup_bot.handlers.error_handler import SUPPRESSED_EXCEPTIONS
from mitup_bot.models import User
from mitup_bot.monitoring import MetricKey
from mitup_bot.utils.mitup_types import TMitupContext
from tests.helpers import MockDbSession, StubMitupContext


@pytest.mark.parametrize(
    "error, message",
    [(error, message) for error, messages in SUPPRESSED_EXCEPTIONS.items() for message in messages],
)
async def test_errors_ignored(error: type, message: str, context: StubMitupContext):
    error_obj = error(message)

    await error_handler.handler(cast(TMitupContext, context), error_obj, Env.DEV)
    await context.metrics_engine.flush_metrics()

    context.metrics_engine.assert_metrics_not_emited([MetricKey.FAULT], [1])


async def test_handle_inactive_user_error(context: StubMitupContext, user: User, mock_session: MockDbSession):
    assert user.is_active
    assert user.id is not None

    mock_session.add_object(user)

    await error_handler.handler(cast(TMitupContext, context), InactiveUserInteraction(user.id, private=True), Env.DEV)
    await context.metrics_engine.flush_metrics()

    assert not user.is_active
    context.metrics_engine.assert_metrics_emited([MetricKey.INACTIVE_USER_SET], [1], add_handler_dimensions=False)
    context.metrics_engine.assert_metrics_not_emited([MetricKey.FAULT], [1])


async def test_handle_error_for_uncaght_exception(context: StubMitupContext):
    context.prepare_handler_metrics({"SomeDimension": "SomeValue", "SomeOtherDimension": "SomeOtherValue"})

    try:
        # We need to raise the exception to ahve exec_info available when the error is handled
        raise RuntimeError()
    except RuntimeError:
        await error_handler.handler(cast(TMitupContext, context), RuntimeError(), Env.DEV)
        await context.metrics_engine.flush_metrics()

    context.metrics_engine.assert_metrics_emited(
        [MetricKey.FAULT],
        [1],
        add_handler_dimensions=False,
        exception="RuntimeError",
    )
    context.metrics_engine.assert_handler_metrics_emitted(
        [MetricKey.FAULT.with_prefix("RuntimeError"), MetricKey.FAULT],
        [1, 1],
        exception="RuntimeError",
    )
