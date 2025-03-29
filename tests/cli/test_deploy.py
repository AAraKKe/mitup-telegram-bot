import base64
import json
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, cast
from unittest import mock

import click
import pytest
from click.testing import CliRunner
from mypy_boto3_ecs import ECSClient
from mypy_boto3_ecs.type_defs import DescribeServicesResponseTypeDef
from mypy_boto3_lambda import LambdaClient
from rich.console import Capture
from rich.status import Status

from mitup_bot.cli.commands import deploy
from tests.helpers import console


@dataclass
class DeploymentContext:
    """A wrapper that allows us to easily setup tests for the deploy command"""

    # Cannot initialize fields to mutable types. Default factory is a lambda that generates the list of dicts
    update_function_responses: list[dict[str, Any]] = field(default_factory=lambda: [{}])
    get_function_responses: list[dict[str, Any]] = field(default_factory=lambda: [{}])
    invoke_lambda_responses: list[dict[str, Any]] = field(default_factory=lambda: [{}])
    describe_task_responses: list[dict[str, Any]] = field(default_factory=lambda: [{}])
    register_task_responses: list[dict[str, Any]] = field(default_factory=lambda: [{}])
    update_ecs_responses: list[dict[str, Any]] = field(default_factory=lambda: [{}])
    describe_services_responses: list[dict[str, Any]] = field(default_factory=lambda: [{}])

    def __post_init__(self):
        # Add clients in post_init to avoid having a class variable set to a mock
        # that we wil be manipulating in each test
        self.lambda_client = mock.MagicMock(spec=LambdaClient)
        self.ecs_client = mock.MagicMock(spec=ECSClient)

    def __assert_method_called(self, n: int, mock_method: mock.MagicMock | None, *args, **kwargs):
        if mock_method is None:
            # If we cannot find the method it means it has not been called.
            # We need to assert we are actually asking for no calls made
            assert n == 0
            return

        if n:
            mock_method.assert_called_with(*args, **kwargs)
            assert len(mock_method.call_args_list) == n
        else:
            mock_method.assert_not_called()

    def assert_lambda_called(self, method: str, *args, n: int = 1, **kwargs):
        mock_method: mock.MagicMock | None = getattr(self.lambda_client, method, None)
        self.__assert_method_called(n, mock_method, *args, **kwargs)

    def assert_ecs_called(self, method: str, n: int = 1, *args, **kwargs):
        mock_method: mock.MagicMock | None = getattr(self.ecs_client, method)
        self.__assert_method_called(n, mock_method, *args, **kwargs)

    def setup_lambda_returns(self):
        self.lambda_client.update_function_code.side_effect = self.update_function_responses
        self.lambda_client.get_function.side_effect = self.get_function_responses
        self.lambda_client.invoke.side_effect = self.invoke_lambda_responses

    def setup_ecs_returns(self):
        self.ecs_client.describe_task_definition.side_effect = self.describe_task_responses
        self.ecs_client.describe_services.side_effect = self.describe_services_responses
        self.ecs_client.update_service.side_effect = self.update_ecs_responses
        self.ecs_client.register_task_definition.side_effect = self.register_task_responses

    @contextmanager
    def setup_mock(
        self,
    ) -> Generator[tuple[LambdaClient, ECSClient, Capture]]:
        client_mapping = {
            "lambda": self.lambda_client,
            "ecs": self.ecs_client,
        }
        with mock.patch("mitup_bot.cli.commands.deploy.boto3.client") as mock_client:
            # Allow patching for when we need it testing the full command
            mock_client.side_effect = lambda service, region_name: client_mapping[service]
            self.setup_lambda_returns()
            self.setup_ecs_returns()
            with deploy.console().capture() as capture:
                # Also capture the output to the console
                yield (
                    self.lambda_client,
                    self.ecs_client,
                    capture,
                )


@pytest.fixture(autouse=True)
def mock_sleep():
    # Just make sure we do not sleep for tests
    with mock.patch("mitup_bot.cli.commands.deploy.time"):
        yield


def test_update_lambda_code_succeeds_after_retry():
    context = DeploymentContext(
        get_function_responses=[
            # First call
            {"Configuration": {"LastUpdateStatus": "InProgress"}},
            # Second call
            {"Configuration": {"LastUpdateStatus": "InProgress"}},
            # Third call
            {"Configuration": {"LastUpdateStatus": "Successful"}},
        ],
    )

    with context.setup_mock() as (function, ecs, capture):
        deploy.update_lambda_code(function, "MyLambda", "MyImage")

    context.assert_lambda_called(
        "update_function_code",
        FunctionName="MyLambda",
        ImageUri="MyImage",
        Publish=True,
    )
    context.assert_lambda_called("get_function", n=3, FunctionName="MyLambda")
    assert console.text_with_ansi_codes("[bold green]✔︎ Lambda MyLambda function code updated[/]") in capture.get()


def test_update_lambda_code_fails_aborting_command():
    context = DeploymentContext(
        get_function_responses=[
            # First call
            {"Configuration": {"LastUpdateStatus": "InProgress"}},
            # Second call
            {
                "Configuration": {
                    "LastUpdateStatus": "Failed",
                    "LastUpdateStatusReason": "SomeReason",
                }
            },
        ]
    )

    with context.setup_mock() as (function, ecs, capture):
        with pytest.raises(click.Abort):
            deploy.update_lambda_code(function, "MyLambda", "MyImage")

    context.assert_lambda_called(
        "update_function_code",
        FunctionName="MyLambda",
        ImageUri="MyImage",
        Publish=True,
    )
    context.assert_lambda_called("get_function", n=2, FunctionName="MyLambda")

    captured = capture.get()
    assert (
        console.text_with_ansi_codes(
            "[bold red]✘ Failed updating code for lambda MyLambda for the following reason:[/]"
        )
        in captured
    )
    assert "SomeReason" in captured


@pytest.mark.parametrize(
    "responses",
    [
        # Missing on first call
        [{"Configuration": {}}],
        # Missing on later calls
        [{"Configuration": {"LastUpdateStatus": "InProgress"}}, {"Configuration": {}}],
    ],
    ids=["missing_on_first_call", "missing_on_later_calls"],
)
def test_update_lambda_code_fails_without_update_status(responses: list[dict[str, Any]]):
    context = DeploymentContext(get_function_responses=responses)

    with context.setup_mock() as (function, _, capture):
        with pytest.raises(click.Abort):
            deploy.update_lambda_code(function, "MyLambda", "MyImage")


def test_invoke_lambda_succeeds():
    # Setup the log generate by the lambda on invocation
    start_json = {"type": "platform.start"}
    duration_json = {
        "type": "platform.report",
        "record": {"metrics": {"durationMs": 123.123}},
    }
    log_result = f"{json.dumps(start_json)}\nThis is a log line\n\n\nAnd this is another\n{json.dumps(duration_json)}"

    # Lambda payload
    expected_payload = json.dumps({"action": "upgrade", "revision": "head"})

    context = DeploymentContext(
        invoke_lambda_responses=[
            {
                "StatusCode": 200,
                "LogResult": base64.b64encode(
                    bytes(log_result, encoding="utf-8"),
                ),
            }
        ]
    )

    with context.setup_mock() as (function, ecs, capture):
        deploy.invoke_lambda(function, "MyLambda")

    context.assert_lambda_called(
        "invoke",
        FunctionName="MyLambda",
        LogType="Tail",
        Payload=expected_payload,
    )

    # Validate output in the correct order to report
    expected_output = "\n".join(
        [
            console.rule("[bold]Log[/bold]").replace("\n", ""),
            "This is a log line",
            "And this is another",
            console.rule("[bold]Lambda MyLambda run in 123.123 ms").replace("\n", ""),
            console.text_with_ansi_codes("[bold green]✔︎ Lambda MyLambda finished successfully[/]"),
        ]
    )  # Always ends with a new line
    assert expected_output in capture.get()


@pytest.mark.parametrize("error_code", [400, 200], ids=["failure_400", "200_with_function_error"])
def test_invoke_lambda_fails(error_code: int):
    payload = json.dumps({"action": "upgrade", "revision": "head"})

    context = DeploymentContext(
        invoke_lambda_responses=[
            {
                "StatusCode": error_code,
                "FunctionError": "FunctionIsBroken",
                "LogResult": base64.b64encode(b"Some log result"),
            }
        ]
    )

    with context.setup_mock() as (function, ecs, capture):
        with pytest.raises(click.Abort):
            deploy.invoke_lambda(function, "MyLambda")

    context.assert_lambda_called(
        "invoke",
        FunctionName="MyLambda",
        LogType="Tail",
        Payload=payload,
    )

    assert "✘ Error invoking lambda MyLambda: FunctionIsBroken" in capture.get()


def test_invoke_lambda_fails_on_execution_and_error_is_logged():
    start_json = {"type": "platform.start"}
    error_log = {
        "log_level": "ERROR",
        "errorMessage": "This thing is broken!",
        "stackTrace": ["line1", "line2"],
    }
    log_result = f"{json.dumps(start_json)}\nThis is a log line\n\n\nAnd this is another\n{json.dumps(error_log)}"

    payload = json.dumps({"action": "upgrade", "revision": "head"})

    context = DeploymentContext(
        invoke_lambda_responses=[
            {
                "StatusCode": 500,
                "FunctionError": "FunctionIsBroken",
                "LogResult": base64.b64encode(
                    bytes(log_result, encoding="utf-8"),
                ),
            }
        ]
    )

    with context.setup_mock() as (function, ecs, capture):
        with pytest.raises(click.Abort):
            deploy.invoke_lambda(function, "MyLambda")

    context.assert_lambda_called(
        "invoke",
        FunctionName="MyLambda",
        LogType="Tail",
        Payload=payload,
    )

    captured = capture.get()

    assert console.text_with_ansi_codes("[bold red]Lambda failed execution: This thing is broken![/]") in captured
    assert "Stack trace:\nline1\nline2\n" in captured
    assert console.text_with_ansi_codes("[bold red]✘ Error invoking lambda MyLambda: FunctionIsBroken[/]") in captured


def test_register_task_definition_succeeds():
    # Setup parameters
    family = "MyTask"
    image = "MyNewImage"
    role = "SomeRoleArn"
    task_arn = "MyTaskArn"
    container_definitions = [{"image": "SomePreviousImage"}]

    context = DeploymentContext(
        describe_task_responses=[
            {
                "taskDefinition": {
                    "containerDefinitions": container_definitions,
                    "executionRoleArn": role,
                    "taskDefinitionArn": task_arn,
                },
            }
        ],
        register_task_responses=[
            {
                "taskDefinition": {
                    "revision": 20,
                    "taskDefinitionArn": task_arn,
                },
                "ResponseMetadata": {"HTTPStatusCode": 200},
            }
        ],
    )

    with context.setup_mock() as (function, ecs, capture):
        result = deploy.register_task_definition(ecs, family, image)

    context.assert_ecs_called("describe_task_definition", taskDefinition=family)
    context.assert_ecs_called(
        "register_task_definition", family=family, containerDefinitions=container_definitions, executionRoleArn=role
    )
    assert result == task_arn

    captured = capture.get()
    assert f"ECR image: {image}" in captured
    assert (
        console.text_with_ansi_codes("[bold green]✔︎ New task definition defined for family 'MyTask', revision: 20[/]")
        in captured
    )


def test_register_task_definition_when_failing():
    # Setup parameters
    family = "MyTask"
    image = "MyNewImage"
    role = "SomeRoleArn"
    task_arn = "MyTaskArn"
    container_definitions = [{"image": "SomePreviousImage"}]

    context = DeploymentContext(
        describe_task_responses=[
            {
                "taskDefinition": {
                    "containerDefinitions": container_definitions,
                    "executionRoleArn": role,
                    "taskDefinitionArn": task_arn,
                },
            }
        ],
        register_task_responses=[
            {
                "ResponseMetadata": {"HTTPStatusCode": 404},
            }
        ],
    )

    with context.setup_mock() as (function, ecs, capture):
        with pytest.raises(click.Abort):
            deploy.register_task_definition(ecs, family, image)

    context.assert_ecs_called("describe_task_definition", taskDefinition=family)
    context.assert_ecs_called(
        "register_task_definition",
        family=family,
        containerDefinitions=container_definitions,
        executionRoleArn=role,
    )

    captured = capture.get()
    assert f"ECR image: {image}" in captured
    assert (
        console.text_with_ansi_codes(
            f"[bold red]✘ Error registering task definition for {family!r}: [StatusCode: 404][/]"
        )
        in captured
    )


def task_definition_response_factory(container_definitions=True, execution_role=True):
    response = {"taskDefinition": {}}
    if container_definitions:
        response["taskDefinition"]["containerDefinitions"] = [{"image": "MyNewImage"}]
    if execution_role:
        response["taskDefinition"]["executionRoleArn"] = "role"

    return response


def register_task_response_factory(status_code=True, revision=True, arn=True):
    response = {"ResponseMetadata": {}, "taskDefinition": {}}
    if status_code:
        response["ResponseMetadata"]["HTTPStatusCode"] = 200
    if revision:
        response["taskDefinition"]["revision"] = 20
    if arn:
        response["taskDefinition"]["taskDefinitionArn"] = "arn"

    return response


@pytest.mark.parametrize(
    "responses",
    [
        {
            "describe_task_responses": [task_definition_response_factory(container_definitions=False)],
            "register_task_responses": [register_task_response_factory()],
        },
        {
            "describe_task_responses": [task_definition_response_factory(execution_role=False)],
            "register_task_responses": [register_task_response_factory()],
        },
        {
            "describe_task_responses": [task_definition_response_factory()],
            "register_task_responses": [register_task_response_factory(status_code=False)],
        },
        {
            "describe_task_responses": [task_definition_response_factory()],
            "register_task_responses": [register_task_response_factory(revision=False)],
        },
        {
            "describe_task_responses": [task_definition_response_factory()],
            "register_task_responses": [register_task_response_factory(arn=False)],
        },
    ],
    ids=[
        "missing_container_definitions",
        "missing_execution_role",
        "missing_status_code",
        "missing_revision",
        "missing_arn",
    ],
)
def test_register_task_definition_fails_with_missing_response_data(responses: dict[str, list[dict[str, Any]]]):
    # Setup parameters
    family = "MyTask"
    image = "MyNewImage"

    context = DeploymentContext(**responses)

    with context.setup_mock() as (function, ecs, capture):
        with pytest.raises(click.Abort):
            deploy.register_task_definition(ecs, family, image)


def create_service_description(
    desired: int, running: int, pending: int, arn: str, n_tasks: int, state="IN_PROGRESS", reason="All Good"
) -> dict[str, Any]:
    return {
        "services": [
            {
                "desiredCount": desired,
                "runningCount": running,
                "pendingCount": pending,
                "deployments": [
                    {"taskDefinition": arn, "rolloutState": state, "rolloutStateReason": reason} for _ in range(n_tasks)
                ],
            },
        ]
    }


def service_from_state(state: str, reason: str) -> dict[str, Any]:
    return create_service_description(1, 1, 0, "MyTask", 1, state, reason)


@pytest.mark.parametrize(
    "describe_service_response",
    [
        # No deployments
        create_service_description(1, 1, 0, "myTask", 0),
        # No valid task arn
        create_service_description(1, 1, 0, "wrongTaskArn", 1),
        # More than one task
        create_service_description(1, 1, 0, "myTask", 2),
    ],
    ids=["missing_deployments", "invalid_task_definition", "multiple_deployments_per_task"],
)
def test_update_deployment_status_fails(describe_service_response: DescribeServicesResponseTypeDef):
    with pytest.raises(click.Abort):
        deploy.update_status_for_deployment(Status("Some message"), describe_service_response, "myTask")


def test_update_deployment_status_succeeds():
    status = Status("Some message")
    service = cast(DescribeServicesResponseTypeDef, create_service_description(1, 1, 0, "myTask", 1))
    result = deploy.update_status_for_deployment(status, service, "myTask")

    assert result.get("rolloutState") == "IN_PROGRESS"
    assert (
        status.status
        == "Deployment: IN_PROGRESS | Tasks: [ Desired: [bold]1[/], Running: [bold]1[/], Pending: [bold]0[/] ]"
    )


def test_update_ecs_service_succeeds():
    context = DeploymentContext(update_ecs_responses=[{"ResponseMetadata": {"HTTPStatusCode": 200}}])

    with context.setup_mock() as (function, ecs, capture):
        deploy.update_ecs_service(ecs, "MyTask", "MyService", "MyCluster")

    context.assert_ecs_called("update_service", cluster="MyCluster", service="MyService", taskDefinition="MyTask")
    assert console.text_with_ansi_codes("[bold green]✔︎ ECS service 'MyService' has been updated[/]") in capture.get()


def test_update_ecs_service_fails():
    context = DeploymentContext(update_ecs_responses=[{"ResponseMetadata": {"HTTPStatusCode": 404}}])

    with context.setup_mock() as (function, ecs, capture):
        with pytest.raises(click.Abort):
            deploy.update_ecs_service(ecs, "MyTask", "MyService", "MyCluster")

    context.assert_ecs_called("update_service", cluster="MyCluster", service="MyService", taskDefinition="MyTask")
    assert (
        console.text_with_ansi_codes("[bold red]✘ Error updating ECS service 'MyService'. StatusCode: 404[/]")
        in capture.get()
    )


def test_deployment_fails_while_waiting():
    context = DeploymentContext(
        describe_services_responses=[
            service_from_state("IN_PROGRESS", "All good"),
            service_from_state("FAILED", "This broke!"),
        ]
    )

    with context.setup_mock() as (function, ecs, capture):
        with pytest.raises(click.Abort):
            deploy.waiting_for_deployment_to_finish(ecs, "MyCluster", "MyService", "MyTask")

    context.assert_ecs_called("describe_services", services=["MyService"], cluster="MyCluster", n=2)
    assert "Failed latest deployment: This broke!" in capture.get()


def test_deployment_succeeds_after_waiting():
    context = DeploymentContext(
        describe_services_responses=[
            service_from_state("IN_PROGRESS", "All good"),
            service_from_state("IN_PROGRESS", "All good"),
            service_from_state("COMPLETED", "All good"),
        ]
    )

    with context.setup_mock() as (function, ecs, capture):
        deploy.waiting_for_deployment_to_finish(ecs, "MyCluster", "MyService", "MyTask")

    context.assert_ecs_called("describe_services", services=["MyService"], cluster="MyCluster", n=3)
    assert (
        console.text_with_ansi_codes("[bold green]✔︎ ECS service 'MyService' successfuly deployed![/]") in capture.get()
    )


def service_from_state_with_missing_data(rollout_state=True, rollout_state_reason=True) -> dict[str, Any]:
    service = service_from_state("IN_PROGRESS", "All good")
    if not rollout_state:
        service["services"][0]["deployments"][0].pop("rolloutState")
    if not rollout_state_reason:
        service = service_from_state("FAILED", "Nah")
        service["services"][0]["deployments"][0].pop("rolloutStateReason")

    return service


@pytest.mark.parametrize(
    "responses",
    [
        # Missing rollout state in the first call
        [service_from_state_with_missing_data(rollout_state=False)],
        # Missing rollout state in the second call
        [service_from_state("IN_PROGRESS", "All good"), service_from_state_with_missing_data(rollout_state=False)],
        # Missing rollout state reason
        [service_from_state_with_missing_data(rollout_state_reason=False)],
    ],
    ids=["missing_rollout_state_first_call", "missing_rollout_state_second_call", "missing_failed_reason"],
)
def test_deployment_fails_when_missing_response_data(responses: list[dict[str, Any]]):
    context = DeploymentContext(describe_services_responses=responses)

    with context.setup_mock() as (function, ecs, capture):
        with pytest.raises(click.Abort):
            deploy.waiting_for_deployment_to_finish(ecs, "MyCluster", "MyService", "MyTask")


@pytest.mark.parametrize(
    "side_effects,number_of_calls,exit_code",
    [
        # Update migrations lambda fails
        [(click.Abort, None, [None, None], [None, None], [None, None]), (1, 0, 0, 0, 0), 1],
        # Invoke lambda fails
        [(None, click.Abort, [None, None], [None, None], [None, None]), (1, 1, 0, 0, 0), 1],
        # Register bot task fails
        [(None, None, [click.Abort, None], [None, None], [None, None]), (1, 1, 1, 0, 0), 1],
        # Update service fails
        [(None, None, [None, None], [click.Abort, None], [None, None]), (1, 1, 1, 1, 0), 1],
        # Waiting for deployment fails
        [(None, None, [None, None], [None, None], [click.Abort, None]), (1, 1, 1, 1, 1), 1],
        # Register recurrent events task fails
        [(None, None, [None, click.Abort], [None, None], [None, None]), (1, 1, 2, 1, 1), 1],
        # Update events service fails
        [(None, None, [None, None], [None, click.Abort], [None, None]), (1, 1, 2, 2, 1), 1],
        # Waiting for events deployment fails
        [(None, None, [None, None], [None, None], [None, click.Abort]), (1, 1, 2, 2, 2), 1],
        # All successfull
        [(None, None, [None, None], [None, None], [None, None]), (1, 1, 2, 2, 2), 0],
    ],
    ids=[
        "fail_on_update_migrations_lambda",
        "fail_on_invoke_lambda",
        "fail_on_register_task_definition",
        "fail_on_update_ecs_service",
        "fail_on_waiting_for_deployment",
        "fail_on_register_recurrent_events_task",
        "fail_on_update_events_service",
        "fail_on_waiting_for_events_deployment",
        "all_successfull",
    ],
)
def test_command_chain_is_not_broken(
    side_effects: tuple[click.Abort | None, ...], number_of_calls: tuple[int, ...], exit_code: int
):
    # This tests the entire command to make sure that what we expect to happen when launched is happening
    # Since we have covered all cases, in this test we will mock all methods in the invoke module and just evaluate
    # the flow
    with (
        mock.patch("mitup_bot.cli.commands.deploy.update_lambda_code") as update_lambda_code,
        mock.patch("mitup_bot.cli.commands.deploy.invoke_lambda") as invoke_lambda,
        mock.patch("mitup_bot.cli.commands.deploy.register_task_definition") as register_task_definition,
        mock.patch("mitup_bot.cli.commands.deploy.update_ecs_service") as update_ecs_service,
        mock.patch(
            "mitup_bot.cli.commands.deploy.waiting_for_deployment_to_finish"
        ) as waiting_for_deployment_to_finish,
    ):
        update_lambda_code.side_effect = side_effects[0]
        invoke_lambda.side_effect = side_effects[1]
        register_task_definition.side_effect = side_effects[2]
        update_ecs_service.side_effect = side_effects[3]
        waiting_for_deployment_to_finish.side_effect = side_effects[4]

        with DeploymentContext().setup_mock() as (func, ecs, capture):
            runner = CliRunner()
            result = runner.invoke(
                deploy.cli, ["--migrations-image", "migrations_image:latest", "--bot-image", "bot_image:latest"]
            )

            assert result.exit_code == exit_code
            assert len(update_lambda_code.call_args_list) == number_of_calls[0]
            assert len(invoke_lambda.call_args_list) == number_of_calls[1]
            assert len(register_task_definition.call_args_list) == number_of_calls[2]
            assert len(update_ecs_service.call_args_list) == number_of_calls[3]
            assert len(waiting_for_deployment_to_finish.call_args_list) == number_of_calls[4]
