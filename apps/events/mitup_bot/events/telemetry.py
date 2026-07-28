"""Shared log-field helpers for the recurrent-events plane."""


def error_type_name(error: BaseException) -> str:
    """Name `error`'s class the way an `error_type` log field carries it.

    Fully qualified (`module.QualName`) so one Logs Insights `stats by error_type` reads the events
    plane together with the bot's fault records, which name the class the same way, and so two
    same-named classes from different packages stay distinct.
    """
    return f"{type(error).__module__}.{type(error).__qualname__}"
