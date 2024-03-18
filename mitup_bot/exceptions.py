from telegram import Update

from mitup_bot.callback_data import CallbackData


class HandlerRegisteredError(AttributeError):
    def __init__(self, key: str):
        super().__init__(f"A handler with ID {key!r} has already been registered and is marked as unique")


class WrongCommandNameError(ValueError):
    pass


class WrongMessageNameError(ValueError):
    pass


class HandlerNotRegistered(RuntimeError):
    def __init__(self, name: str):
        super().__init__(f"The handler(s) {name!r} has not been registered")


class MalformedCallbackData(RuntimeError):
    def __init__(self, handler: str, callback_data: CallbackData) -> None:
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
        super().__init__(f"User with Telegram id {id} not found in database")


class CallbackQueryNotSet(RuntimeError):
    def __init__(self, update: Update):
        super().__init__(f"Expected callback data in Telegram Update not available: {update.to_json()}")
