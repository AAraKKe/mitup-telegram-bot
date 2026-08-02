"""Shared telemetry helpers for the recurrent-events plane."""

from collections import Counter
from collections.abc import Iterable

from mitup_bot.supporter import SupporterLevel


def supporter_level_counts(levels: Iterable[SupporterLevel]) -> dict[str, int]:
    """Count `levels` per tier with every tier present, zeros included.

    The summary lines these counts ride are charted per tier; a map that omits quiet tiers makes
    a zero day indistinguishable from a day the field was missing.
    """
    counts = Counter(levels)
    return {level.value: counts.get(level, 0) for level in SupporterLevel}


def error_type_name(error: BaseException) -> str:
    """Name `error`'s class the way an `error_type` log field carries it.

    Fully qualified (`module.QualName`) so one Logs Insights `stats by error_type` reads the events
    plane together with the bot's fault records, which name the class the same way, and so two
    same-named classes from different packages stay distinct.
    """
    return f"{type(error).__module__}.{type(error).__qualname__}"
