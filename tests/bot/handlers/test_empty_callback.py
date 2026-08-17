import pytest
from telegram import Update
from telegram.ext import Application, BaseHandler

from mitup_bot.callback_data import CallbackData
from mitup_bot.custom_context import MitupContext
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.handlers.empty_callback import EmptyCallbackHandlerId
from mitup_bot.utils import callbacks as cb
from tests.helpers import HandlerContext, UpdateRequest, call_handler

# The wire form the calendar's decorative cells carry, spelled out rather than rendered from
# `cb.EMPTY`: every keyboard already sitting in a chat sends this exact string, so it is what the
# handler has to keep matching.
DECORATIVE_WIRE_FORM = "empty;empty:0"

# Data in the current wire format that no button of ours mints.
UNBOUND_CALLBACK = CallbackData(action="ghost", entity="button")


def first_matching_handler(app: Application, update: Update) -> BaseHandler[Update, MitupContext, object]:
    """The handler PTB would run for `update`: the first match, walking groups then insertion order."""
    return next(
        handler
        for group in sorted(app.handlers)
        for handler in app.handlers[group]
        if (matched := handler.check_update(update)) is not None and matched is not False
    )


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.EMPTY)], indirect=True)
async def test_a_tap_on_a_decorative_button_is_acknowledged_and_nothing_more(
    update: Update, handler_context: HandlerContext
):
    """A weekday header is a label the caller's client still reports as a button press. The
    acknowledgement is what clears the spinner it spun; it carries no text and no alert, so nothing
    appears, and no other call is made because the user asked for nothing."""
    context, _ = await call_handler(EmptyCallbackHandlerId.EMPTY_CALLBACK, handler_context=handler_context)

    assert update.callback_query is not None
    context.bot.answer_callback_query.assert_awaited_once_with(update.callback_query.id)
    awaited = [name for name, mocked in context.api.mock_mapping.items() if mocked.call_count]
    assert awaited == []


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=cb.EMPTY)], indirect=True)
def test_a_decorative_tap_is_routed_to_the_no_op_rather_than_the_fallback(update: Update, app: Application):
    """The fallback answers every callback query nothing else claimed, and it treats unmatched data as
    a defect rather than an interaction — so the no-op only helps if it is matched first."""
    HandlersRegistry.bind(app)

    assert update.callback_query is not None
    assert update.callback_query.data == DECORATIVE_WIRE_FORM
    assert first_matching_handler(app, update) is HandlersRegistry.get_handler(EmptyCallbackHandlerId.EMPTY_CALLBACK)


@pytest.mark.parametrize("update", [UpdateRequest(callback_query=UNBOUND_CALLBACK.with_id(3))], indirect=True)
def test_data_bound_to_no_handler_still_reaches_the_fallback(update: Update, app: Application):
    """Swallowing the placeholder must not blunt the signal the fallback exists for: a button we
    shipped without a handler, or a forged payload, still has to fail loudly."""
    HandlersRegistry.bind(app)

    # `bind` appends the catch-all last in group 0, after every registered handler.
    assert first_matching_handler(app, update) is app.handlers[0][-1]
