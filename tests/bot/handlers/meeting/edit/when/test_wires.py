"""The callback wire strings the When feature renders and the retired ones it still answers.

Every gesture of the two halves was renamed, and a keyboard already sitting in a chat keeps the
string it was built with — a message from last month sends the old form and nothing about it says
so. Each row below pins both directions: the string the bot renders today, and the string it must
keep accepting. Dropping an alias later fails a test that names the wire it removed, rather than
silently breaking every button older than the deploy.
"""

import datetime as dt
import re

import pytest

from mitup_bot.callback_data import CallbackData, DateCallbackData
from mitup_bot.utils import callbacks as cb

MEETING_ID = 42
PICKED_DATE = dt.date(2026, 7, 16)

# (callback, the wire it renders now, the wire it used to render)
RENAMED_WIRES: list[tuple[CallbackData, str, str]] = [
    (cb.OPEN_START_EDITOR, "open;meet_start:42", "set;meet_st:42"),
    (cb.NAVIGATE_START_CALENDAR, "nav;meet_start:42;date:2026-07-16", "edit;meet_date:42;date:2026-07-16"),
    (cb.PICK_START_DATE, "pick;meet_start:42;date:2026-07-16", "set;md:42;date:2026-07-16"),
    (cb.OPEN_START_TIME_PROMPT, "ask_time;meet_start:42", "edit;meet_time:42"),
    (cb.CANCEL_START_EDIT, "cancel;meet_start:42", "cancel;meet_st:42"),
    (cb.OPEN_END_EDITOR, "open;meet_end:42", "set;meet_et:42"),
    (cb.REOPEN_END_EDITOR, "reopen;meet_end:42", "edit;meet_edt:42"),
    (cb.NAVIGATE_END_CALENDAR, "nav;meet_end:42;date:2026-07-16", "edit;meet_ed:42;date:2026-07-16"),
    (cb.PICK_END_DATE, "pick;meet_end:42;date:2026-07-16", "set;med:42;date:2026-07-16"),
    (cb.OPEN_END_TIME_PROMPT, "ask_time;meet_end:42", "edit;meet_et:42"),
    (cb.CANCEL_END_EDIT, "cancel;meet_end:42", "cancel;meet_dur:42"),
]

WIRE_IDS = [current for _, current, _ in RENAMED_WIRES]


def addressed(callback: CallbackData) -> CallbackData:
    """The callback as a button carries it: with the meeting id, and a date where the wire has one."""
    with_id = callback.with_id(MEETING_ID)
    return with_id.with_date(PICKED_DATE) if isinstance(with_id, DateCallbackData) else with_id


@pytest.mark.parametrize("callback,current_wire,_retired_wire", RENAMED_WIRES, ids=WIRE_IDS)
def test_a_renamed_callback_renders_its_new_wire(callback: CallbackData, current_wire: str, _retired_wire: str):
    assert str(addressed(callback)) == current_wire


@pytest.mark.parametrize("callback,_current_wire,retired_wire", RENAMED_WIRES, ids=WIRE_IDS)
def test_a_renamed_callback_still_answers_its_retired_wire(
    callback: CallbackData, _current_wire: str, retired_wire: str
):
    """A button drawn before the rename sends the retired string and must still route and parse."""
    match = re.match(callback.pattern, retired_wire)
    assert match is not None, f"{retired_wire!r} no longer matches {callback.pattern!r}"
    assert callback.parse(match).id == MEETING_ID


def test_the_start_editor_reopen_wire_refuses_the_shared_edit_wire():
    """`edit;meeting` must not be an alias of REOPEN_START_EDITOR.

    That wire is the Edit screen's own, and the reopen handler is an entry point of the start
    conversation. A conversation's entry points are matched ahead of plain handlers, so aliasing it
    here would divert every Edit-button tap in the bot into the start-time flow. Buttons drawn with
    the old form keep reaching the Edit screen, which is where they visibly go.
    """
    assert re.match(cb.REOPEN_START_EDITOR.pattern, "edit;meeting:42") is None
    assert str(cb.REOPEN_START_EDITOR.with_id(MEETING_ID)) == "reopen;meet_start:42"
