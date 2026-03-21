from dataclasses import dataclass
from typing import Any

from mitup_bot.monitoring.units import MetricUnit


@dataclass(frozen=True)
class MetricRecord:
    name: str
    value: float
    unit: MetricUnit
    dimensions: frozenset[tuple[str, str]]
    properties: dict[str, Any]

    @property
    def dimensions_dict(self) -> dict[str, str]:
        return dict(self.dimensions)
