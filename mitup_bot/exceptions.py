from telegram import Update

from mitup_bot.callback_data import CallbackData
from mitup_bot.callback_id import CallbackId


class HandlerRegisteredError(AttributeError):
    def __init__(self, callback: CallbackId):
        super().__init__(f"A handler with ID {callback!r} has already been registered and is marked as unique")


class WrongCommandNameError(ValueError):
    pass


class WrongMessageNameError(ValueError):
    pass


class HandlerNotRegistered(RuntimeError):
    def __init__(self, name: CallbackId | list[CallbackId]):
        handler_list = ", ".join([repr(n) for n in name]) if isinstance(name, list) else repr(name)
        super().__init__(f"The handler(s) {handler_list!r} has not been registered")


class MalformedCallbackData(RuntimeError):
    def __init__(self, handler: CallbackId, callback_data: CallbackData) -> None:
        super().__init__(f"Callback data {callback_data!r} received in handler {handler!r} is malformed.")


class EffectiveUserNotSet(RuntimeError):
    def __init__(self, update: Update):
        super().__init__(f"Expected user in Telegram Update not available: {update.to_json()}")


class EffectiveChatNotSet(RuntimeError):
    def __init__(self, update: Update):
        super().__init__(f"Expected chat in Telegram Update not available: {update.to_json()}")


class EffectiveMessageNotSet(RuntimeError):
    def __init__(self, update: Update):
        super().__init__(f"Expected message in Telegram Update not available: {update.to_json()}")


class UserNotFound(RuntimeError):
    def __init__(self, tg_user_id: int):
        super().__init__(f"User with Telegram id {tg_user_id} not found in database")


class CallbackQueryNotSet(RuntimeError):
    def __init__(self, update: Update):
        super().__init__(f"Expected callback data in Telegram Update not available: {update.to_json()}")


class InvalidUserData(RuntimeError): ...


class UpdateNotDefined(ValueError):
    def __init__(self):
        super().__init__("MitupContext Update was requested but it is not defined.")


class ContextPropertyNotSetError(ValueError): ...


class ContextPropertyConversionError(ValueError):
    def __init__(self, context: str, property: str, value: str):
        super().__init__(
            f"Failed to convert property {property!r} in context {context!r} to the expected type. "
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


class InlineQueryNotSetError(ValueError):
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
    def __init__(self, user_id: int, private: bool):
        self.user_id = user_id
        self.private = private
        super().__init__(f"The user {user_id} is inactive and interacted with the bot")


class CallbackQueryTextTooLong(ValueError):
    def __init__(self, text: str):
        length = len(text)
        super().__init__(f"Callback query text is too long [{length}, max: 200]: {text!r}")
