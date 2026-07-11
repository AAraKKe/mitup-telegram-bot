from unittest import mock

import pytest

from mitup_bot import api_guards, api_wrapper, guards
from mitup_bot.api_wrapper import UpdateGuards, UpdateGuardsNotRegisteredError


def test_register_update_guards_wires_the_api_slot():
    with mock.patch("mitup_bot.api_wrapper.set_update_guards") as set_guards:
        api_guards.register_update_guards()

    (registered,) = set_guards.call_args.args
    assert isinstance(registered, UpdateGuards)
    assert registered.chat is guards.chat
    assert registered.valid_inline_query is guards.valid_inline_query
    assert registered.valid_callback_query is guards.valid_callback_query


def test_get_update_guards_raises_when_unregistered():
    with mock.patch("mitup_bot.api_wrapper.__update_guards", None):
        with pytest.raises(UpdateGuardsNotRegisteredError):
            api_wrapper.get_update_guards()


def test_registered_guards_are_returned_by_the_accessor():
    api_guards.register_update_guards()

    resolved = api_wrapper.get_update_guards()

    assert resolved.chat is guards.chat
    assert resolved.valid_inline_query is guards.valid_inline_query
    assert resolved.valid_callback_query is guards.valid_callback_query
