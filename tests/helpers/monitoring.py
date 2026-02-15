import inspect
import json
from collections.abc import Sequence
from typing import Any, override

from aws_embedded_metrics.environment.local_environment import LocalEnvironment
from aws_embedded_metrics.logger.metrics_context import MetricsContext
from aws_embedded_metrics.logger.metrics_logger import MetricsLogger
from aws_embedded_metrics.sinks.stdout_sink import StdoutSink
from aws_embedded_metrics.unit import Unit

from mitup_bot.custom_context import MitupContext
from mitup_bot.monitoring import (
    Dimensionality,
    Feature,
    MetricKey,
    MitupMetricsEngine,
    MitupMetricsLogger,
    properties_from_update,
)

TSinkContainer = list[dict[str, Any]]


class InMemorySink(StdoutSink):
    def __init__(self, container: TSinkContainer):
        super().__init__()
        self.container = container

    @override
    def accept(self, context: MetricsContext):
        serialized_content = self.serializer.serialize(context)
        for content in serialized_content:
            self.container.append(json.loads(content))


class StubMetrics(MitupMetricsLogger):
    def __init__(self, container: TSinkContainer | None = None):
        self.metrics_container: TSinkContainer = [] if container is None else container
        self.sink = InMemorySink(self.metrics_container)

        async def build_env():
            env = LocalEnvironment()
            env.sink = self.sink
            return env

        super().__init__(build_env)

    @override
    def new(self) -> MetricsLogger:
        """
        Creates a new StubMetrics object mainitaining the same in memory sink to be
        able to assert from a single entry point during testing.
        """
        return StubMetrics(self.metrics_container)

    def __build_expected_body(
        self,
        names: Sequence[str | MetricKey],
        namespace: str,
        values: Sequence[float] | Sequence[Sequence[float]] | None = None,
        units: Sequence[Unit] | None = None,
        dimensions: list[dict[str, str]] | None = None,
        properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        targets: dict[str, Any] = properties or {}
        for dimension in dimensions or []:
            targets |= dimension

        targets = {str(k): v for k, v in targets.items()}

        if values is not None:
            for index, value in enumerate(values):
                targets[str(names[index])] = value

        # Build CloudWatchMetrics body
        cloudwatch: dict[str, Any] = {
            "Namespace": namespace,
            # Force string conversaion in name in case comparison fails with StrEnum
            "Metrics": [{"Name": str(name)} for name in names],
        }
        for index, unit in enumerate(units or [Unit.COUNT] * len(names)):
            cloudwatch["Metrics"][index]["Unit"] = unit.value
        # Sort metrics to ensure consistent order
        cloudwatch["Metrics"] = sorted(cloudwatch["Metrics"], key=lambda x: x["Name"])

        expected_dimensions = [list(dimension.keys())[0] for dimension in dimensions or []]
        cloudwatch["Dimensions"] = [sorted(expected_dimensions)] if expected_dimensions else [[]]

        result = {**targets, "CloudWatchMetrics": [dict(sorted(cloudwatch.items()))]}
        return dict(sorted(result.items()))

    def __build_actual_body(self) -> TSinkContainer:
        # Take the emitted metrics from the container and remove timestamps, not interesting for testing
        result: TSinkContainer = []

        for metric in self.metrics_container:
            current = dict(metric.items())
            current["CloudWatchMetrics"] = current["_aws"]["CloudWatchMetrics"]
            # Flatten dimensions
            current["CloudWatchMetrics"][0]["Dimensions"] = [
                sorted(sum(current["CloudWatchMetrics"][0]["Dimensions"], []))
            ]
            # Sort metrics
            current["CloudWatchMetrics"][0]["Metrics"] = sorted(
                current["CloudWatchMetrics"][0]["Metrics"], key=lambda x: x["Name"]
            )
            current.pop("_aws")
            # If we have an exception, lets remove the traceback and error message
            if "exception" in current:
                current["exception"].pop("traceback")
                current["exception"].pop("error_str")
            result.append(current)

        return [dict(sorted(element.items())) for element in result]

    def assert_metrics_emited(
        self,
        names: Sequence[str | MetricKey],
        values: Sequence[float] | Sequence[Sequence[float]] | None = None,
        units: Sequence[Unit] | None = None,
        namespace: str | None = None,
        dimensions: dict[str, str | Feature] | None = None,
        properties: dict[str, Any] | None = None,
        exception: type[Exception] | str | None = None,
        times: int = 1,
        negative_case: bool = False,
    ):
        """
        Asserts that the specified metrics have been emitted.

        Args:
            names (list[str | MetricKey]): A list of metric names or MetricKey objects.
            values (list[float] | None, optional): A list of metric values. Defaults to None.
            units (list[Unit] | None, optional): A list of metric units. Defaults to None.
            namespace (str | None, optional): The namespace for the metrics. Defaults to None.
            dimensions (dict[str, str | Feature] | None, optional): A dictionary of dimensions for the metrics.
                Defaults to None.
            properties (dict[str, Any] | None, optional): A dictionary of properties for the metrics.
                Defaults to None.
            exception: (type[Exception] | str | None, optional): The exception that was raised. Defaults to None.
            negative_case (bool, optional): If True, asserts that the metrics have not been emitted.
                Defaults to False.
            add_handler_dimensions (bool, optional): If True, adds handler dimensions to the dimensions dictionary.
                Defaults to True.
            add_context_properties (bool, optional): If True, adds context properties to the properties dictionary.
                Defaults to True.

        Raises:
            AssertionError: If the expected metrics are not found or if unexpected metrics are emitted.
        """
        assert values is None or len(names) == len(values), (
            f"The amount of names and values should match. Names: {len(names)}, Values: {len(values)}"
        )
        assert units is None or len(names) == len(units), (
            f"The amount of names and units should match. Names: {len(names)}, Values: {len(units)}"
        )

        if exception is not None:
            properties = properties or {}
            if isinstance(exception, str):
                properties["exception"] = {"error_type": exception}
            else:
                module = inspect.getmodule(exception)
                assert module is not None, "The exception module could not be found."
                exc_type = f"{module.__name__}.{exception.__name__}"
                properties["exception"] = {"error_type": exc_type}

        dimensions_list = [{k: str(v) if isinstance(v, Feature) else v} for k, v in (dimensions or {}).items()]

        expected = self.__build_expected_body(
            names, namespace or self.context.namespace, values, units, dimensions_list, properties
        )
        actual = self.__build_actual_body()

        emitted_list = "\n- ".join(str(element) for element in actual)

        if negative_case:
            assert all(expected != cw_metrics for cw_metrics in actual), (
                f"Unexpected metrics emitted.\nNot expected:\n- {expected}\nEmitted:\n- {emitted_list}"
            )
        else:
            found = sum(expected == cw_metrics for cw_metrics in actual)
            assert found == times, (
                f"Expected metrics emitted {times} times. But emitted {found} times."
                f"\nExpected:\n- {expected}\nEmitted:\n- {emitted_list}"
            )


class StubMetricsEngine(MitupMetricsEngine[StubMetrics]):
    @property
    def parent_context(self) -> MitupContext | None:
        return getattr(self, "_parent_context", None)

    @parent_context.setter
    def parent_context(self, parent_context: MitupContext):
        self._parent_context = parent_context

    @property
    def container(self) -> TSinkContainer:
        return sum((logger.sink.container for logger in self.loggers.values()), [])

    def add_handler_dimensions(self, dimensionality: Dimensionality) -> Dimensionality:
        """
        Adds handler dimensions, if set on the parent context, to the provided list of dimensions. If no dimensions are
        provided, the handler dimensions will be returned. If no parent context is provided, i.e. the metrics have
        not been created from a context, the dimensions will be returned as is.

        Args:
            dimensions (list[dict[str, str]] | None): The list of dimensions to append the handler dimensions to.

        Returns:
            list[dict[str, str]]: The updated list of dimensions with the handler dimensions added.
        """

        if self.parent_context is None:
            return dimensionality

        return dimensionality + self.parent_context.handler_dimensionality

    def add_update_properties(self, properties: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        This is a helper method that attaches the properties to the context defined when it is
        built from an update.

        If the context is not created from a parent context, it will return the properties as is.
        Otherwise, it will return the properties with the context properties attached.

        Args:
            properties (dict[str, Any] | None): The set of properties to be attached to the context. Defaults to None.

        Returns:
            dict[str, Any]: The properties with the context properties attached.
        """
        if self.parent_context is None:
            return properties or {}

        return (properties or {}) | properties_from_update(self.parent_context.telegram_update)

    def assert_metrics_emited(
        self,
        names: Sequence[str | MetricKey],
        values: Sequence[float] | None = None,
        units: Sequence[Unit] | None = None,
        namespace: str | None = None,
        dimensions: dict[str, str | Feature] | None = None,
        properties: dict[str, Any] | None = None,
        exception: type[Exception] | str | None = None,
        times: int = 1,
        negative_case: bool = False,
        add_handler_dimensions: bool = True,
        add_update_properties: bool = True,
    ):
        """
        Asserts that the specified metrics have been emitted.

        Args:
            names (list[str | MetricKey]): A list of metric names or MetricKey objects.
            values (list[float] | None, optional): A list of metric values. Defaults to None.
            units (list[Unit] | None, optional): A list of metric units. Defaults to None.
            namespace (str | None, optional): The namespace for the metrics. Defaults to None.
            dimensions (dict[str, str | Feature] | None, optional): A dictionary of dimensions for the metrics.
                Defaults to None.
            properties (dict[str, Any] | None, optional): A dictionary of properties for the metrics.
                Defaults to None.
            exception: (type[Exception] | str | None, optional): The exception that was raised. Defaults to None.
            negative_case (bool, optional): If True, asserts that the metrics have not been emitted.
                Defaults to False.
            add_handler_dimensions (bool, optional): If True, adds handler dimensions to the dimensions dictionary.
                Defaults to True.
            add_context_properties (bool, optional): If True, adds context properties to the properties dictionary.
                Defaults to True.

        Raises:
            AssertionError: If the expected metrics are not found or if unexpected metrics are emitted.
        """
        dimensionality = Dimensionality(**(dimensions or {}))
        if add_handler_dimensions:
            dimensionality = self.add_handler_dimensions(dimensionality)

        if add_update_properties:
            properties = self.add_update_properties(properties)

        assert dimensionality in self.loggers or negative_case, f"There is no logger with dimensions {dimensionality}"

        self.get_logger(dimensionality).assert_metrics_emited(
            names=names,
            values=values,
            units=units,
            namespace=namespace,
            dimensions=dimensionality.dimensions,
            properties=properties,
            exception=exception,
            times=times,
            negative_case=negative_case,
        )

    def assert_handler_metrics_emitted(
        self,
        names: Sequence[str | MetricKey],
        values: Sequence[float] | None = None,
        units: Sequence[Unit] | None = None,
        exception: type[Exception] | str | None = None,
        times: int = 1,
    ):
        """
        Asserts that the specified handler metrics have been emitted.

        Args:
            names (list[str | MetricKey]): A list of metric names or MetricKey objects.
            values (list[float] | None, optional): A list of metric values. Defaults to None.
            units (list[Unit] | None, optional): A list of metric units. Defaults to None.
            exception: (type[Exception] | str | None, optional): The exception that was raised. Defaults to None.

        Raises:
            AssertionError: If the expected metrics are not found or if unexpected metrics are emitted.
        """
        self.assert_metrics_emited(
            names,
            values,
            units,
            exception=exception,
            add_handler_dimensions=True,
            add_update_properties=True,
            times=times,
        )

    def assert_feature_metrics_emitted(
        self,
        feature: Feature,
        times: int = 1,
    ):
        """Assert wheather a given feature metric has been emitted.

        Args:
            feature (Feature): The feature metric that should have been emitted
            times (int, optional): How many times the metric has been emitted. Defaults to 1.
        """
        dimensions = {"Feature": feature.value}

        self.assert_metrics_emited(
            [MetricKey.COUNT],
            [1.0],
            [Unit.COUNT],
            dimensions=dimensions,
            add_handler_dimensions=False,
            add_update_properties=True,
            times=times,
        )

    def assert_feature_metrics_not_emitted(
        self,
        feature: Feature,
    ):
        """Assert wheather a given feature metric has not been emitted.

        Args:
            feature (Feature): The feature metric that should not have been emitted
        """
        dimensions = {"Feature": feature.value}

        self.assert_metrics_emited(
            [MetricKey.COUNT],
            [1.0],
            [Unit.COUNT],
            dimensions=dimensions,
            add_handler_dimensions=False,
            add_update_properties=True,
            times=0,
            negative_case=True,
        )

    def assert_metrics_not_emited(
        self,
        names: list[str | MetricKey],
        values: list[float] | None = None,
        units: list[Unit] | None = None,
        namespace: str | None = None,
        dimensions: dict[str, str | Feature] | None = None,
        properties: dict[str, Any] | None = None,
        exception: type[Exception] | str | None = None,
        add_handler_dimensions: bool = False,
        add_update_properties: bool = True,
    ):
        """
        Assert a given set of metrics have not been emitted. The arguments are the same as for `assert_metrics_emited`.
        """
        self.assert_metrics_emited(
            names,
            values,
            units,
            namespace,
            dimensions,
            properties,
            exception=exception,
            negative_case=True,
            add_handler_dimensions=add_handler_dimensions,
            add_update_properties=add_update_properties,
        )
