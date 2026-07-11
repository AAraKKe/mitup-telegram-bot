"""Unit tests for custom exception classes in mitup_bot/exceptions.py."""

from mitup_bot.exceptions import (
    ContextPropertyConversionError,
    ContextPropertyNotSetError,
    InlineQueryNotSetError,
    InvalidUserData,
    PositiveNumberFilterError,
    TimezoneClientAlreadyInitializedError,
    UpdateNotDefined,
)


def test_update_not_defined_message():
    exc = UpdateNotDefined()

    assert exc.args[0] == "MitupContext Update was requested but it is not defined."


def test_context_property_conversion_error_message():
    exc = ContextPropertyConversionError(
        context="MyContext",
        property="count",
        value="bad_value",
        expected_type=int,
    )

    assert "MyContext" in str(exc)
    assert "count" in str(exc)
    assert "bad_value" in str(exc)
    assert "int" in str(exc)


def test_timezone_client_already_initialized_error_message():
    exc = TimezoneClientAlreadyInitializedError()

    assert exc.args[0] == "The timezone client has already been configured."


def test_positive_number_filter_error_message():
    exc = PositiveNumberFilterError()

    assert exc.args[0] == "The input must be a positive number."


def test_inline_query_not_set_error_message():
    exc = InlineQueryNotSetError()

    assert exc.args[0] == "InlineQueryId is not set but expected."


def test_invalid_user_data_is_runtime_error():
    # InvalidUserData uses ... as body — verify it can be raised and caught as RuntimeError
    exc = InvalidUserData("bad user data")
    assert isinstance(exc, RuntimeError)


def test_context_property_not_set_error_is_value_error():
    # ContextPropertyNotSetError uses ... as body — verify it can be raised and caught as ValueError
    exc = ContextPropertyNotSetError("property not set")
    assert isinstance(exc, ValueError)
