from telegram import Update

from mitup_bot.callback_data import CallbackData
from mitup_bot.handler_id import HandlerId


class HandlerRegisteredError(AttributeError):
    def __init__(self, callback: HandlerId):
        super().__init__(f"A handler with ID {callback!r} has already been registered and is marked as unique")


class WrongCommandNameError(ValueError):
    pass


class WrongMessageNameError(ValueError):
    pass


class HandlerNotRegistered(RuntimeError):
    def __init__(self, name: HandlerId | list[HandlerId]):
        handler_list = ", ".join([repr(n) for n in name]) if isinstance(name, list) else repr(name)
        super().__init__(f"The handler(s) {handler_list!r} has not been registered")


class GuardError(RuntimeError):
    """Base class for input-validation failures raised by ``guards.py``.

    Subclasses ``RuntimeError`` so existing ``except RuntimeError`` / ``except Exception`` behaviour
    is preserved, while letting the centralized error handler classify guard faults under a single base.
    """


class MalformedCallbackData(GuardError):
    def __init__(self, handler: HandlerId, callback_data: CallbackData):
        super().__init__(f"Callback data {callback_data!r} received in handler {handler!r} is malformed.")


class EffectiveUserNotSet(GuardError):
    def __init__(self, update: Update):
        super().__init__(f"Expected user in Telegram Update not available: {update.to_json()}")


class EffectiveChatNotSet(GuardError):
    def __init__(self, update: Update):
        super().__init__(f"Expected chat in Telegram Update not available: {update.to_json()}")


class EffectiveMessageNotSet(GuardError):
    def __init__(self, update: Update):
        super().__init__(f"Expected message in Telegram Update not available: {update.to_json()}")


class UserNotFound(GuardError):
    def __init__(self, tg_user_id: int):
        super().__init__(f"User with Telegram id {tg_user_id} not found in database")


class CallbackQueryNotSet(GuardError):
    def __init__(self, update: Update):
        super().__init__(f"Expected callback data in Telegram Update not available: {update.to_json()}")


class InvalidUserData(RuntimeError): ...  # pragma: no cover


class UpdateNotDefined(ValueError):
    def __init__(self):
        super().__init__("MitupContext Update was requested but it is not defined.")


class ContextPropertyNotSetError(ValueError): ...  # pragma: no cover


class ContextPropertyConversionError(ValueError):
    def __init__(self, context: str, property: str, value: str | int | bool, expected_type: type):
        super().__init__(
            f"Failed to convert property {property!r} in context {context!r} to the expected type {expected_type!r}. "
            f"Value received: {value!r}"
        )


class MeetupNotFound(IOError):
    def __init__(self, meetup_id: int):
        super().__init__(f"Meetup with id {meetup_id} not found in database.")


class GeocodeClientAlreadyInitializedError(RuntimeError):
    def __init__(self):
        super().__init__("The geocode client has already been configured.")


class TimezoneClientAlreadyInitializedError(RuntimeError):
    def __init__(self):
        super().__init__("The timezone client has already been configured.")


class IncorrectKeyError(ValueError):
    def __init__(self):
        super().__init__("The key is not correct.")


class IncorrectGeocodeKeyError(RuntimeError):
    def __init__(self):
        super().__init__("Geocode key is not set correctly.")


class IncorrectTimezoneKeyError(RuntimeError):
    def __init__(self):
        super().__init__("Timezone key is not set correctly.")


class IncorrectCoordinatesError(ValueError):
    def __init__(self):
        super().__init__("The latitude and longitude are not set correctly")


class GeocodeClientNotConfiguredError(RuntimeError):
    def __init__(self):
        super().__init__("The geocode client must be configured before using it.")


class TimezoneClientNotConfiguredError(RuntimeError):
    def __init__(self):
        super().__init__("The timezone client must be configured before using it.")


class PositiveNumberFilterError(RuntimeError):
    def __init__(self):
        super().__init__("The input must be a positive number.")


class MetricsNotSetError(ValueError):
    pass


class InlineQueryNotSetError(GuardError, ValueError):
    # Also subclasses ValueError so any `except ValueError` still catches it.
    def __init__(self):
        super().__init__("InlineQueryId is not set but expected.")


class AnswerInlineQueryError(RuntimeError):
    def __init__(self, query: str):
        super().__init__(f"Error when answering inline query: {query}")


class NoMessageAvailable(ValueError):
    pass


class InvalidLanguageError(ValueError):
    def __init__(self, idx: int):
        super().__init__(f"Language {idx} is not supported and was received as callabck id.")


class InactiveUserInteraction(RuntimeError):
    # Carries the TELEGRAM user id (raise sites pass user.tg_user_id).
    def __init__(self, tg_user_id: int, private: bool):
        self.tg_user_id = tg_user_id
        self.private = private
        super().__init__(f"The user {tg_user_id} is inactive and interacted with the bot")


class UserPendingDeletion(RuntimeError):
    """A user whose data deletion is pending interacted with the bot.

    Deliberately NOT a ``GuardError``: a pending deletion is an expected business state, not an
    internal fault, so the error handler answers the interaction with a dedicated alert instead of
    emitting fault metrics and rebuilding a main menu for a dying account.

    Carries the user's language so the alert renders without another DB round-trip.
    """

    def __init__(self, tg_user_id: int, lang: str):
        self.tg_user_id = tg_user_id
        self.lang = lang
        super().__init__(f"The user {tg_user_id} has a pending deletion request and interacted with the bot")


class CallbackQueryTextTooLong(ValueError):
    def __init__(self, text: str):
        length = len(text)
        super().__init__(f"Callback query text is too long [{length}, max: 200]: {text!r}")


class TokenEncryptionNotConfigured(RuntimeError):
    def __init__(self):
        super().__init__("Patreon token encryption was used before configure_token_encryption() supplied a Fernet key.")


class PatreonNotConfigured(RuntimeError):
    def __init__(self):
        super().__init__("Patreon support was used before a [patreon] config section was supplied at startup.")


class PatreonApiError(RuntimeError):
    """A Patreon HTTP call returned an unexpected status or body."""

    def __init__(self, message: str):
        super().__init__(f"Patreon API error: {message}")


class PatreonTokenRevoked(RuntimeError):
    """A token refresh failed with OAuth ``invalid_grant`` — the user disconnected the app on
    Patreon's side. The daily job treats this as a business event, not a transient fault."""

    def __init__(self):
        super().__init__("Patreon refused the refresh token with invalid_grant (app disconnected on Patreon's side).")


class PatreonStateExpired(ValueError):
    """The OAuth ``state`` token is authentic but older than its TTL — the inline button was
    tapped long after it was rendered. The callback tells the user to tap the button again.

    Carries ``age_seconds`` (the token's authentic minted-to-now age) so the callback can tell
    slow consent from clock skew from a button left sitting for hours."""

    def __init__(self, age_seconds: float):
        self.age_seconds = age_seconds
        super().__init__(f"The Patreon OAuth state token has expired ({age_seconds:.0f}s old).")


class PatreonStateInvalid(ValueError):
    """The OAuth ``state`` token failed signature validation (tampered, truncated, or signed with
    a different key). Distinct from expiry so the callback can render a different message."""

    def __init__(self):
        super().__init__("The Patreon OAuth state token is invalid.")
