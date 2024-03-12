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
