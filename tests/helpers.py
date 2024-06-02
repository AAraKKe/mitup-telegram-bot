import datetime as dt
import inspect
import json
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Any, cast, override
from unittest import mock

from aws_embedded_metrics.environment.local_environment import LocalEnvironment
from aws_embedded_metrics.logger.metrics_context import MetricsContext
from aws_embedded_metrics.logger.metrics_logger import MetricsLogger
from aws_embedded_metrics.sinks.stdout_sink import StdoutSink
from aws_embedded_metrics.unit import Unit
from telegram import Location, Update
from telegram.ext import Application, CallbackContext

from mitup_bot.callback_data import CallbackData
from mitup_bot.callback_id import CallbackId
from mitup_bot.custom_context import ContextId, MitupContext, MitupUserData
from mitup_bot.handlers import HandlersRegistry
from mitup_bot.models.meetups import Meetup
from mitup_bot.monitoring import Feature, MetricKey
from mitup_bot.views import MitupView

StubMitupContext = MitupContext[mock.MagicMock, "StubMetrics"]
"""MitupContext type for testing purposes"""

StubMitupApp = Application[mock.MagicMock, StubMitupContext, MitupUserData, dict, dict, None]
"""Application type for testing purposes"""


class AnyFloat(float):
    """Use this in assertions for metrics where the value is not important"""

    def __eq__(self, other: Any) -> bool:
        return True


@dataclass
class UpdateRequest:
    """
    A data class representing a Telegram update we want injected as a fixture.

    Every type of update managed in the bot will include an user, a chat and a message. Since the most common type of
    update handled by the bot, message defaults to False. Otherwise, the update will be a pure message update.

    Args:
        user (bool, optional): Whether to include user information in the update request. Defaults to True.
        chat (bool, optional): Whether to include chat information in the update request. Defaults to True.
        message (bool, optional): Whether to include message information in the update request. Defaults to True.
        callback_data (CallbackData | bool, optional): Defines whether or not the update should include callback data.
            If True, a default CallbackQuery will be added. If a CallbackData object is provided, it will be used to
            generate the CallbackQuery. Defaults to False.
        inline_query (str, optional): The inline query string. Defaults to "".
    """

    user: bool = True
    chat: bool = True
    message: bool = True
    message_text: str | None = None
    location: Location | None = None
    callback_query: CallbackData | bool = False
    command: str | bool = False
    inline_query: str = ""


@dataclass
class MockApi:
    """
    This is a helper class that helps aserring if we have called api methods via patching those methods and exposing
    easy to use assert methods.

    We do not rely on testing the bot methods called but instead we assert calls on the methods in `mitup_bot.api`. The
    intention is to keep testing at a higher abstraction level working with views instead of having to test the low
    level telegram library methods.

    The api is instantiated from the MockApi.start() method. The module path where the api module is being imported
    needs to be provided to start to be able to start the patching. The method patching is released when out of context.
    """

    send_message_mock: mock.AsyncMock
    edit_message_mock: mock.AsyncMock

    @classmethod
    @contextmanager
    def start(cls, module_path: str) -> Generator["MockApi", None, None]:
        with (
            mock.patch(f"{module_path}.api.edit_message") as edit_patch,
            mock.patch(f"{module_path}.api.send_message") as send_patch,
        ):
            yield MockApi(send_message_mock=send_patch, edit_message_mock=edit_patch)

    def assert_send_message_called(
        self, context: mock.MagicMock | CallbackContext, update: Update, view: MitupView | str, times: int = 1
    ):
        self.assert_method_called(self.send_message_mock, context, update, view, times)

    def assert_edit_message_called(
        self, context: mock.MagicMock | CallbackContext, update: Update, view: MitupView | str, times: int = 1
    ):
        self.assert_method_called(self.edit_message_mock, context, update, view, times)

    def assert_send_message_not_called(self):
        self.send_message_mock.assert_not_called()

    def assert_edit_message_not_called(self):
        self.edit_message_mock.assert_not_called()

    def assert_method_called(
        self,
        method: mock.AsyncMock,
        context: mock.MagicMock | CallbackContext,
        update: Update,
        view: MitupView | str,
        times: int,
    ):
        # Validate that the update has been properly generated
        assert update.effective_chat is not None
        assert update.effective_message is not None

        if times == 1:
            method.assert_awaited_once_with(context, update, view)
        else:
            # If more than one time we need to assert that we have called it the amount of times requested
            # and at least one of them with the appropriate arguments
            assert len(method.call_args_list) == times, f"Expected {times} call but found {len(method.call_args_list)}"
            expected_call = mock.call(context, update, view)
            assert any(
                expected_call == call for call in method.await_args_list
            ), f"Expected call {expected_call} not found in method"


class InMemorySink(StdoutSink):
    def __init__(self, container: list[dict[str, Any]]):
        super().__init__()
        self.container = container

    @override
    def accept(self, context: MetricsContext):
        serialized_content = self.serializer.serialize(context)
        for content in serialized_content:
            self.container.append(json.loads(content))


class StubMetrics(MetricsLogger):
    def __init__(self, context: MetricsContext, container: list[dict[str, Any]] | None = None):
        self.metrics_container: list[dict[str, Any]] = [] if container is None else container
        self.sink = InMemorySink(self.metrics_container)
        self._parent_context = None

        async def build_env():
            env = LocalEnvironment()
            env.sink = self.sink
            return env

        super().__init__(build_env, context)

    @override
    def new(self) -> MetricsLogger:
        """
        Creates a new StubMetrics object mainitaining the same in memory sink to be
        able to assert from a single entry point during testing.
        """
        return StubMetrics(MetricsContext.empty(), self.metrics_container)

    @property
    def parent_context(self) -> MitupContext | None:
        return self._parent_context

    @parent_context.setter
    def parent_context(self, parent_context: MitupContext):
        self._parent_context = parent_context

    def __build_expected_body(
        self,
        names: Sequence[str | MetricKey],
        namespace: str,
        values: Sequence[float] | None = None,
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

    def __build_actual_body(self) -> list[dict[str, Any]]:
        # Take the emitted metrics from the container and remove timestamps, not interesting for testing
        result: list[dict[str, Any]] = []
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
        values: Sequence[float] | None = None,
        units: Sequence[Unit] | None = None,
        namespace: str | None = None,
        dimensions: dict[str, str | Feature] | None = None,
        properties: dict[str, Any] | None = None,
        exception: type[Exception] | str | None = None,
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
        assert values is None or len(names) == len(
            values
        ), f"The amount of names and values should match. Names: {len(names)}, Values: {len(values)}"
        assert units is None or len(names) == len(
            units
        ), f"The amount of names and units should match. Names: {len(names)}, Values: {len(units)}"

        if add_handler_dimensions:
            dimensions = self.add_handler_dimensions(dimensions)

        if add_update_properties:
            properties = self.add_update_properties(properties)

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
            assert all(
                expected != cw_metrics for cw_metrics in actual
            ), f"Unexpected metrics emitted.\nNot expected:\n- {expected}\nEmitted:\n- {emitted_list}"
        else:
            assert any(
                expected == cw_metrics for cw_metrics in actual
            ), f"Expected metrics not found.\nExpected:\n- {expected}\nEmitted:\n- {emitted_list}"

    def assert_handler_metrics_emitted(
        self,
        names: Sequence[str | MetricKey],
        values: Sequence[float] | None = None,
        units: Sequence[Unit] | None = None,
        exception: type[Exception] | str | None = None,
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
        add_handler_dimensions: bool = True,
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

    def add_handler_dimensions(self, dimensions: dict[str, str] | None = None) -> dict[str, str]:
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
            return dimensions or {}

        return (dimensions or {}) | self.parent_context.handler_dimensions

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

        callback_data = (
            self.parent_context.telegram_update.callback_query.data
            if self.parent_context.telegram_update and self.parent_context.telegram_update.callback_query
            else None
        )
        parent_properties = {
            "UserId": self.parent_context._user_id,
            "ChatId": self.parent_context._chat_id,
            "CallbackData": callback_data,
            "Update": self.parent_context.telegram_update.to_dict(),
        }
        return (properties or {}) | parent_properties


def build_context(
    update: Update,
    app: StubMitupApp,
    with_meeting_id: dict[ContextId, int] | None = None,
) -> StubMitupContext:
    if update.effective_message:
        update.effective_message.set_bot(app.bot)
    context = cast(StubMitupContext, MitupContext.from_update(update, app))

    # Allow the StubMetrics to access the context it was built for
    context.metrics.parent_context = context

    for context_id, meeting_id in (with_meeting_id or {}).items():
        assert context.user_data is not None
        context.user_data.store_meeting_id(context_id, meeting_id)

    return context


def build_metrics() -> StubMetrics:
    return StubMetrics(MetricsContext.empty())


async def call_handler(
    update: Update,
    app: StubMitupApp,
    handler_id: CallbackId,
    with_meeting_id: dict[ContextId, int] | None = None,
) -> tuple[StubMitupContext, Enum | None]:
    context = build_context(update, app, with_meeting_id)

    handler = HandlersRegistry.get_handler(handler_id)

    # Allow natural handling of the request data provided on the update
    check_result = handler.check_update(update)
    assert check_result is not None, "This update would not be processed by the handler!"
    assert check_result is not False, "This update would not be processed by the handler!"

    return context, await handler.handle_update(update, app, check_result, context)


def create_meetup(
    id: int,
    title: str = "Default title",
    description: str = "Default description",
    datetime: dt.datetime | None = None,
) -> Meetup:
    return Meetup(id=id, title=title, description=description, datetime=datetime)
