import inspect
from typing import Any

from mitup_bot.monitoring import MetricKey, MetricRecord, MetricsClient, MetricUnit
from mitup_bot.monitoring.backend import NullBackend


def make_test_metrics_client(base_dimensions: dict[str, str] | None = None) -> MetricsClient:
    """Build a MetricsClient suitable for tests.

    Always uses `NullBackend` (no I/O) and enables `record_history=True` so
    `MetricAssertions` can introspect emitted records. Centralizes the test-side
    construction so the production default (`record_history=False`) stays untouched.
    """
    return MetricsClient(NullBackend(), base_dimensions=base_dimensions, record_history=True)


class FlushCountingBackend(NullBackend):
    """NullBackend that counts flush() calls, for asserting flush wiring."""

    def __init__(self):
        self.flush_count = 0

    async def flush(self):
        self.flush_count += 1


class MetricAssertions:
    """Helper for asserting metrics emitted by a MetricsClient in tests."""

    def __init__(self, client: MetricsClient):
        self._client = client

    @property
    def _records(self) -> list[MetricRecord]:
        return self._client.records

    def _matches(
        self,
        record: MetricRecord,
        *,
        name: str | MetricKey,
        value: float | None,
        unit: MetricUnit | None,
        dimensions: dict[str, str] | None,
        dimensions_exact: bool,
        properties: dict[str, Any] | None,
        properties_exact: bool,
        exception: type[Exception] | str | None,
    ) -> bool:
        if record.name != str(name):
            return False

        if value is not None and record.value != value:
            return False

        if unit is not None and record.unit != unit:
            return False

        if dimensions is not None:
            actual_dims = record.dimensions_dict
            if dimensions_exact:
                if actual_dims != dimensions:
                    return False
            else:
                # Subset match: expected dimensions must all be in actual
                if not all(actual_dims.get(k) == v for k, v in dimensions.items()):
                    return False

        if properties is not None:
            actual_props = record.properties
            if properties_exact:
                if actual_props != properties:
                    return False
            else:
                # Subset match: expected properties must all be in actual
                if not all(actual_props.get(k) == v for k, v in properties.items()):
                    return False

        if exception is not None:
            if isinstance(exception, str):
                expected_type = exception
            else:
                module = inspect.getmodule(exception)
                assert module is not None, "The exception module could not be found."
                expected_type = f"{module.__name__}.{exception.__name__}"

            # The fault path names the exception in an `error_type` property; the traceback itself
            # lives on the structlog line, not on the record.
            actual_type = record.properties.get("error_type", "")
            # Support both short name match ("RuntimeError") and fully qualified match
            if expected_type not in actual_type and not actual_type.endswith(expected_type):
                return False

        return True

    def _format_records(self) -> str:
        if not self._records:
            return "  (no records emitted)"
        lines = []
        for r in self._records:
            dims = dict(r.dimensions)
            lines.append(f'  - name="{r.name}", value={r.value}, unit={r.unit.value}, dimensions={dims}')
        return "\n".join(lines)

    def assert_emitted(
        self,
        *,
        name: str | MetricKey,
        value: float | None = None,
        unit: MetricUnit | None = None,
        dimensions: dict[str, str] | None = None,
        dimensions_exact: bool = False,
        properties: dict[str, Any] | None = None,
        properties_exact: bool = False,
        exception: type[Exception] | str | None = None,
        times: int = 1,
    ):
        found = sum(
            self._matches(
                record,
                name=name,
                value=value,
                unit=unit,
                dimensions=dimensions,
                dimensions_exact=dimensions_exact,
                properties=properties,
                properties_exact=properties_exact,
                exception=exception,
            )
            for record in self._records
        )

        if found != times:
            parts = [f'name="{name}"']
            if value is not None:
                parts.append(f"value={value}")
            if unit is not None:
                parts.append(f"unit={unit.value}")
            if dimensions is not None:
                parts.append(f"dimensions={dimensions}")
            if properties is not None:
                parts.append(f"properties={properties}")
            if exception is not None:
                parts.append(f"exception={exception}")
            expected_desc = ", ".join(parts)

            raise AssertionError(
                f'Expected metric "{name}" emitted {times} time(s), found {found}.\n'
                f"Expected: {expected_desc}\n"
                f"Emitted records:\n{self._format_records()}"
            )

    def assert_not_emitted(
        self,
        *,
        name: str | MetricKey,
        value: float | None = None,
        unit: MetricUnit | None = None,
        dimensions: dict[str, str] | None = None,
        dimensions_exact: bool = False,
        properties: dict[str, Any] | None = None,
        properties_exact: bool = False,
        exception: type[Exception] | str | None = None,
    ):
        self.assert_emitted(
            name=name,
            value=value,
            unit=unit,
            dimensions=dimensions,
            dimensions_exact=dimensions_exact,
            properties=properties,
            properties_exact=properties_exact,
            exception=exception,
            times=0,
        )
