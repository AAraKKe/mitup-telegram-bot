"""Shared telemetry helpers for the recurrent-events plane."""

from collections.abc import Mapping

from mitup_bot.monitoring import MetricKey, MetricsClient, MetricUnit
from mitup_bot.supporter import LEVEL_ORDER, SupporterLevel


def error_type_name(error: BaseException) -> str:
    """Name `error`'s class the way an `error_type` log field carries it.

    Fully qualified (`module.QualName`) so one Logs Insights `stats by error_type` reads the events
    plane together with the bot's fault records, which name the class the same way, and so two
    same-named classes from different packages stay distinct.
    """
    return f"{type(error).__module__}.{type(error).__qualname__}"


def emit_per_supporter_level(metrics: MetricsClient, key: MetricKey, counts: Mapping[SupporterLevel, int]):
    """Emit `key` once per supporter level, including the levels this run did not touch.

    The retention windows are per tier, so a destruction count nobody can split by tier cannot
    answer whether a free cohort's ninety-day cliff or a mistakenly aged-out Patron cohort produced
    it — the one question a per-tier policy creates. The tier is a bounded, deliberate dimension of
    four values, and every level is emitted every run so each series stays continuous instead of
    reading as missing data on the runs it took nobody from.
    """
    for level in LEVEL_ORDER:
        metrics.emit(key, counts.get(level, 0), MetricUnit.COUNT, dimensions={"SupporterLevel": level.value})
