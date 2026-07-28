"""The per-update correlation identity and the tally the exit line reports.

The processor is the only choke point both run modes share, but it cannot see routing: PTB
turns every handler exception into a call to its own error plane and reports nothing at all for
an update no handler matched. So the wrapped invocations report back here, and the trace answers
"was anything run, and did it fault" from the outside.

One asyncio task processes one update, and a task runs on its own copy of the context, so the
entry set here is visible to everything downstream and to nothing concurrent.
"""

from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum, auto

import structlog
from telegram import Update

from mitup_bot import db


class UpdateOutcome(StrEnum):
    """What the update amounted to, as the exit line names it."""

    HANDLED = auto()
    UNROUTED = auto()
    FAILED = auto()


@dataclass
class UpdateTrace:
    handlers_run: int = 0
    fault_recorded: bool = False


UPDATE_TRACE: ContextVar[UpdateTrace | None] = ContextVar("update_trace", default=None)


def current_update_trace() -> UpdateTrace | None:
    return UPDATE_TRACE.get()


def record_handler_invocation(*, faulted: bool):
    """Tally one wrapped handler callback against the update being processed.

    An update processed outside the processor (a background task PTB failed on its own) has no
    trace, and nothing is recorded for it.
    """
    trace = UPDATE_TRACE.get()
    if trace is None:
        return
    trace.handlers_run += 1
    trace.fault_recorded = trace.fault_recorded or faulted


def update_outcome(trace: UpdateTrace, *, escaped: bool) -> UpdateOutcome:
    """Name how the update left the processor.

    `UNROUTED` is the one the log has never been able to answer: no handler matched, so the user
    pressed something and nothing happened.
    """
    if escaped or trace.fault_recorded:
        return UpdateOutcome.FAILED
    return UpdateOutcome.HANDLED if trace.handlers_run else UpdateOutcome.UNROUTED


# Bound mid-update with `bind_contextvars`, which — unlike `bound_contextvars` — has no scope of
# its own to expire with, because the value has to outlive the guard, the handler and the commit.
UPDATE_SCOPED_BINDS = ("meeting_id",)


def clear_update_scoped_state():
    """Drop what one update left ambient, before the next one starts.

    At a concurrency cap of 1 PTB awaits every update inside its own fetcher task
    (`Application.__update_fetcher`), so one context serves the whole process and anything set
    mid-update outlives the update that set it: the next one would inherit a meeting it never
    touched and the write phase of a transaction it never opened. Above 1 each update gets a
    task and a context copy of its own — but the cap is configuration, so the boundary is
    enforced here rather than assumed.
    """
    structlog.contextvars.unbind_contextvars(*UPDATE_SCOPED_BINDS)
    db.clear_write_state()


def update_type(update: Update) -> str | None:
    """Name the update after the field Telegram filled in, e.g. `callback_query`."""
    return next((str(name) for name in Update.ALL_TYPES if getattr(update, name, None) is not None), None)


def update_log_context(update: object) -> dict[str, object]:
    """The correlation identity bound before routing, omitting whatever the update lacks.

    Anything can be fed into PTB's update queue, and an update carries no user or chat of its
    own once it comes from a channel, so every field here is conditional.
    """
    if not isinstance(update, Update):
        return {}

    fields: dict[str, object] = {"update_id": update.update_id}
    if (kind := update_type(update)) is not None:
        fields["update_type"] = kind
    if update.effective_user is not None:
        fields["tg_user_id"] = update.effective_user.id
    if update.effective_chat is not None:
        fields["chat_id"] = update.effective_chat.id
    return fields
