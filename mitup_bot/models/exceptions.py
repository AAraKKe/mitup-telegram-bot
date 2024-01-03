class MissingSessionError(RuntimeError):
    def __init__(self):
        super().__init__("Session does not exist")
