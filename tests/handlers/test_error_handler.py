import pytest

from mitup_bot.config import Env
from mitup_bot.exceptions import InactiveUserInteraction
from mitup_bot.handlers import error_handler
from mitup_bot.handlers.error_handler import SUPPRESSED_EXCEPTIONS
from mitup_bot.models import User
from mitup_bot.models.users import UserStatus
from mitup_bot.monitoring import MetricKey
from tests.helpers import MockDbSession, StubMitupContext, create_user
from tests.helpers.monitoring import MetricAssertions


@pytest.mark.parametrize(
    "error, message",
    [(error, message) for error, messages in SUPPRESSED_EXCEPTIONS.items() for message in messages],
)
async def test_errors_ignored(error: type, message: str, context: StubMitupContext, metrics: MetricAssertions):
    error_obj = error(message)

    await error_handler.handler(context, error_obj, Env.DEV)
    await context.metrics.flush()

    metrics.assert_not_emitted(name=MetricKey.FAULT, value=1)


async def test_handle_inactive_user_not_found(
    context: StubMitupContext, mock_session: MockDbSession, metrics: MetricAssertions
):
    """When user_id does not exist in the session, handle_inactive_user returns silently without emitting metrics."""
    # Do not add any user to the session so the lookup returns None
    await error_handler.handle_inactive_user(context, user_id=999)
    await context.metrics.flush()

    metrics.assert_not_emitted(name=MetricKey.INACTIVE_USER_SET, value=1)


async def test_handle_inactive_user_error(
    context: StubMitupContext, user: User, mock_session: MockDbSession, metrics: MetricAssertions
):
    """MEMBER → LEFT transition: the metric MUST fire."""
    assert user.status is UserStatus.MEMBER

    mock_session.add_object(user)

    await error_handler.handler(context, InactiveUserInteraction(user.db_id, private=True), Env.DEV)
    await context.metrics.flush()

    assert user.status is UserStatus.LEFT
    metrics.assert_emitted(name=MetricKey.INACTIVE_USER_SET, value=1)
    metrics.assert_not_emitted(name=MetricKey.FAULT, value=1)


async def test_handle_inactive_user_joined_only_is_noop(
    context: StubMitupContext, mock_session: MockDbSession, metrics: MetricAssertions
):
    """JOINED_ONLY users can never be DM-ed, so the error path must NOT transition them or emit the metric.

    Without this guard, every reminder send to a JOINED_ONLY user would mark them LEFT and
    eventually delete them via user_cleanup, defeating the entire purpose of the new enum.
    """
    user = create_user(id=10, tg_user_id=500, status=UserStatus.JOINED_ONLY)
    mock_session.add_object(user)

    await error_handler.handler(context, InactiveUserInteraction(user.db_id, private=True), Env.DEV)
    await context.metrics.flush()

    assert user.status is UserStatus.JOINED_ONLY
    metrics.assert_not_emitted(name=MetricKey.INACTIVE_USER_SET, value=1)
    metrics.assert_not_emitted(name=MetricKey.FAULT, value=1)


async def test_handle_inactive_user_left_is_noop(
    context: StubMitupContext, mock_session: MockDbSession, metrics: MetricAssertions
):
    """Re-hitting an already-LEFT user must not double-emit the INACTIVE_USER_SET metric."""
    user = create_user(id=11, tg_user_id=501, status=UserStatus.LEFT)
    mock_session.add_object(user)

    await error_handler.handler(context, InactiveUserInteraction(user.db_id, private=True), Env.DEV)
    await context.metrics.flush()

    assert user.status is UserStatus.LEFT
    metrics.assert_not_emitted(name=MetricKey.INACTIVE_USER_SET, value=1)
    metrics.assert_not_emitted(name=MetricKey.FAULT, value=1)


async def test_handle_error_for_uncaght_exception(context: StubMitupContext, metrics: MetricAssertions):
    context.prepare_handler_metrics({"SomeDimension": "SomeValue", "SomeOtherDimension": "SomeOtherValue"})

    try:
        # We need to raise the exception to have exec_info available when the error is handled
        raise RuntimeError()
    except RuntimeError:
        await error_handler.handler(context, RuntimeError(), Env.DEV)
        await context.metrics.flush()

    # emit_global=True emits Fault twice: once with handler dims, once without (for global aggregation)
    metrics.assert_emitted(name=MetricKey.FAULT, value=1, times=2)
    # The prefixed fault is emitted once, with handler dimensions
    metrics.assert_emitted(
        name=MetricKey.FAULT.with_prefix("RuntimeError"),
        value=1,
        dimensions={"SomeDimension": "SomeValue", "SomeOtherDimension": "SomeOtherValue"},
    )
