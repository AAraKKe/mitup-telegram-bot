class HandlerRegisteredError(AttributeError):
    def __init__(self, name: str):
        super().__init__(f"The handler {name} has already been registered")


class WrongCommandNameError(ValueError):
    pass


class HandlerNotRegistered(RuntimeError):
    def __init__(self, name: str):
        super().__init__(f"The hander {name!r} has not been registered")
