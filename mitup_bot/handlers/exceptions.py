class HandlerRegisteredError(AttributeError):
    def __init__(self, key: str):
        super().__init__(
            f"A hanlder with key {key!r} has already been registered and is marked as unique"
        )


class WrongCommandNameError(ValueError):
    pass


class HandlerNotRegistered(RuntimeError):
    def __init__(self, name: str):
        super().__init__(f"The hander {name!r} has not been registered")
