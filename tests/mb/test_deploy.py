import base64
import json
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, cast
from unittest import mock

import pytest
import typer
from mb.main import app
from mypy_boto3_ecs import ECSClient
from mypy_boto3_ecs.type_defs import ServiceDeploymentTypeDef
from mypy_boto3_lambda import LambdaClient
from typer.testing import CliRunner

from mb import console, deploy_ops

cli = CliRunner()


@pytest.fixture(autouse=True)
def plain_console(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pin a wide console so long status lines are not soft-wrapped mid-assertion.
    monkeypatch.setenv("COLUMNS", "200")
    console.configure(plain=True)


def combined(capsys: pytest.CaptureFixture[str]) -> str:
    captured = capsys.readouterr()
    return captured.out + captured.err


@dataclass
class DeploymentContext:
    """A wrapper that allows us to easily setup tests for the deploy command"""

    update_function_responses: list[dict[str, Any]] = field(default_factory=lambda: [{}])
    get_function_responses: list[dict[str, Any]] = field(default_factory=lambda: [{}])
    invoke_lambda_responses: list[dict[str, Any]] = field(default_factory=lambda: [{}])
    describe_task_responses: list[dict[str, Any]] = field(default_factory=lambda: [{}])
    register_task_responses: list[dict[str, Any]] = field(default_factory=lambda: [{}])
    update_ecs_responses: list[dict[str, Any]] = field(default_factory=lambda: [{}])
    list_deployments_responses: list[dict[str, Any]] = field(default_factory=lambda: [{}])
    describe_deployments_responses: list[dict[str, Any]] = field(default_factory=lambda: [{}])

    def __post_init__(self):
        self.lambda_client = mock.MagicMock(spec=LambdaClient)
        self.ecs_client = mock.MagicMock(spec=ECSClient)

    def assert_method_called(self, n: int, mock_method: mock.MagicMock | None, *args, **kwargs):
        if mock_method is None:
            assert n == 0
            return
        if n:
            mock_method.assert_called_with(*args, **kwargs)
            assert len(mock_method.call_args_list) == n
        else:
            mock_method.assert_not_called()

    def assert_lambda_called(self, method: str, *args, n: int = 1, **kwargs):
        mock_method: mock.MagicMock | None = getattr(self.lambda_client, method, None)
        self.assert_method_called(n, mock_method, *args, **kwargs)

    def assert_ecs_called(self, method: str, n: int = 1, *args, **kwargs):
        mock_method: mock.MagicMock | None = getattr(self.ecs_client, method)
        self.assert_method_called(n, mock_method, *args, **kwargs)

    def setup_lambda_returns(self):
        self.lambda_client.update_function_code.side_effect = self.update_function_responses
        self.lambda_client.get_function.side_effect = self.get_function_responses
        self.lambda_client.invoke.side_effect = self.invoke_lambda_responses

    def setup_ecs_returns(self):
        self.ecs_client.describe_task_definition.side_effect = self.describe_task_responses
        self.ecs_client.update_service.side_effect = self.update_ecs_responses
        self.ecs_client.register_task_definition.side_effect = self.register_task_responses
        self.ecs_client.list_service_deployments.side_effect = self.list_deployments_responses
        self.ecs_client.describe_service_deployments.side_effect = self.describe_deployments_responses

    @contextmanager
    def setup_mock(self) -> Generator[tuple[LambdaClient, ECSClient]]:
        client_mapping = {"lambda": self.lambda_client, "ecs": self.ecs_client}
        with mock.patch("mb.deploy_ops.boto3.client") as mock_client:
            mock_client.side_effect = lambda service, region_name: client_mapping[service]
            self.setup_lambda_returns()
            self.setup_ecs_returns()
            yield self.lambda_client, self.ecs_client


def service_deployment(**fields: Any) -> ServiceDeploymentTypeDef:
    return cast(ServiceDeploymentTypeDef, fields)


def deployments_response(*deployments: ServiceDeploymentTypeDef) -> dict[str, Any]:
    return {"serviceDeployments": list(deployments)}


@pytest.fixture(autouse=True)
def mock_time() -> Generator[mock.MagicMock]:
    # Never sleep in tests; freeze monotonic so the heartbeat only fires when a test drives it.
    with mock.patch("mb.deploy_ops.time") as mocked_time:
        mocked_time.monotonic.return_value = 0.0
        yield mocked_time


def test_update_lambda_code_succeeds_after_retry(capsys: pytest.CaptureFixture[str]):
    context = DeploymentContext(
        get_function_responses=[
            {"Configuration": {"LastUpdateStatus": "InProgress"}},
            {"Configuration": {"LastUpdateStatus": "InProgress"}},
            {"Configuration": {"LastUpdateStatus": "Successful"}},
        ],
    )

    with context.setup_mock() as (function, ecs):
        deploy_ops.update_lambda_code(function, "MyLambda", "MyImage")

    context.assert_lambda_called("update_function_code", FunctionName="MyLambda", ImageUri="MyImage", Publish=True)
    context.assert_lambda_called("get_function", n=3, FunctionName="MyLambda")
    assert "✓ Lambda MyLambda function code updated" in combined(capsys)


def test_update_lambda_code_fails_aborting_command(capsys: pytest.CaptureFixture[str]):
    context = DeploymentContext(
        get_function_responses=[
            {"Configuration": {"LastUpdateStatus": "InProgress"}},
            {"Configuration": {"LastUpdateStatus": "Failed", "LastUpdateStatusReason": "SomeReason"}},
        ]
    )

    with context.setup_mock() as (function, ecs):
        with pytest.raises(typer.Abort):
            deploy_ops.update_lambda_code(function, "MyLambda", "MyImage")

    context.assert_lambda_called("update_function_code", FunctionName="MyLambda", ImageUri="MyImage", Publish=True)
    context.assert_lambda_called("get_function", n=2, FunctionName="MyLambda")
    output = combined(capsys)
    assert "✗ Failed updating code for lambda MyLambda for the following reason:" in output
    assert "SomeReason" in output


@pytest.mark.parametrize(
    "responses",
    [
        [{"Configuration": {}}],
        [{"Configuration": {"LastUpdateStatus": "InProgress"}}, {"Configuration": {}}],
    ],
    ids=["missing_on_first_call", "missing_on_later_calls"],
)
def test_update_lambda_code_fails_without_update_status(
    responses: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
):
    context = DeploymentContext(get_function_responses=responses)

    with context.setup_mock() as (function, _):
        with pytest.raises(typer.Abort):
            deploy_ops.update_lambda_code(function, "MyLambda", "MyImage")

    assert "✗ Failed to get the status of the lambda function 'MyLambda'" in combined(capsys)


def test_invoke_lambda_succeeds(capsys: pytest.CaptureFixture[str]):
    start_json = {"type": "platform.start"}
    duration_json = {"type": "platform.report", "record": {"metrics": {"durationMs": 123.123}}}
    log_result = f"{json.dumps(start_json)}\nThis is a log line\n\n\nAnd this is another\n{json.dumps(duration_json)}"

    expected_payload = json.dumps({"action": "upgrade", "revision": "head"})

    context = DeploymentContext(
        invoke_lambda_responses=[
            {"StatusCode": 200, "LogResult": base64.b64encode(bytes(log_result, encoding="utf-8"))}
        ]
    )

    with context.setup_mock() as (function, ecs):
        deploy_ops.invoke_lambda(function, "MyLambda")

    context.assert_lambda_called("invoke", FunctionName="MyLambda", LogType="Tail", Payload=expected_payload)

    output = combined(capsys)
    assert "This is a log line" in output
    assert "And this is another" in output
    assert "Lambda MyLambda run in 123.123 ms" in output
    assert "✓ Lambda MyLambda finished successfully" in output


@pytest.mark.parametrize("error_code", [400, 200], ids=["failure_400", "200_with_function_error"])
def test_invoke_lambda_fails(error_code: int, capsys: pytest.CaptureFixture[str]):
    payload = json.dumps({"action": "upgrade", "revision": "head"})

    context = DeploymentContext(
        invoke_lambda_responses=[
            {"StatusCode": error_code, "FunctionError": "FunctionIsBroken", "LogResult": base64.b64encode(b"Some log")}
        ]
    )

    with context.setup_mock() as (function, ecs):
        with pytest.raises(typer.Abort):
            deploy_ops.invoke_lambda(function, "MyLambda")

    context.assert_lambda_called("invoke", FunctionName="MyLambda", LogType="Tail", Payload=payload)
    assert "✗ Error invoking lambda MyLambda: FunctionIsBroken" in combined(capsys)


def test_invoke_lambda_fails_on_execution_and_error_is_logged(capsys: pytest.CaptureFixture[str]):
    start_json = {"type": "platform.start"}
    error_log = {"log_level": "ERROR", "errorMessage": "This thing is broken!", "stackTrace": ["line1", "line2"]}
    log_result = f"{json.dumps(start_json)}\nThis is a log line\n\n\nAnd this is another\n{json.dumps(error_log)}"

    payload = json.dumps({"action": "upgrade", "revision": "head"})

    context = DeploymentContext(
        invoke_lambda_responses=[
            {
                "StatusCode": 500,
                "FunctionError": "FunctionIsBroken",
                "LogResult": base64.b64encode(bytes(log_result, encoding="utf-8")),
            }
        ]
    )

    with context.setup_mock() as (function, ecs):
        with pytest.raises(typer.Abort):
            deploy_ops.invoke_lambda(function, "MyLambda")

    context.assert_lambda_called("invoke", FunctionName="MyLambda", LogType="Tail", Payload=payload)
    output = combined(capsys)
    assert "Lambda failed execution: This thing is broken!" in output
    assert "Stack trace:" in output
    assert "line1" in output
    assert "line2" in output
    assert "✗ Error invoking lambda MyLambda: FunctionIsBroken" in output


def test_register_task_definition_succeeds(capsys: pytest.CaptureFixture[str]):
    family, image, role, task_arn = "MyTask", "MyNewImage", "SomeRoleArn", "MyTaskArn"
    container_definitions = [{"image": "SomePreviousImage"}]

    context = DeploymentContext(
        describe_task_responses=[
            {
                "taskDefinition": {
                    "containerDefinitions": container_definitions,
                    "executionRoleArn": role,
                    "taskDefinitionArn": task_arn,
                }
            }
        ],
        register_task_responses=[
            {
                "taskDefinition": {"revision": 20, "taskDefinitionArn": task_arn},
                "ResponseMetadata": {"HTTPStatusCode": 200},
            }
        ],
    )

    with context.setup_mock() as (function, ecs):
        result = deploy_ops.register_task_definition(ecs, family, image)

    context.assert_ecs_called("describe_task_definition", taskDefinition=family)
    context.assert_ecs_called(
        "register_task_definition", family=family, containerDefinitions=container_definitions, executionRoleArn=role
    )
    assert result == task_arn
    output = combined(capsys)
    assert f"ECR image: {image}" in output
    assert "✓ New task definition defined for family 'MyTask', revision: 20" in output


def test_register_task_definition_when_failing(capsys: pytest.CaptureFixture[str]):
    family, image, role, task_arn = "MyTask", "MyNewImage", "SomeRoleArn", "MyTaskArn"
    container_definitions = [{"image": "SomePreviousImage"}]

    context = DeploymentContext(
        describe_task_responses=[
            {
                "taskDefinition": {
                    "containerDefinitions": container_definitions,
                    "executionRoleArn": role,
                    "taskDefinitionArn": task_arn,
                }
            }
        ],
        register_task_responses=[{"ResponseMetadata": {"HTTPStatusCode": 404}}],
    )

    with context.setup_mock() as (function, ecs):
        with pytest.raises(typer.Abort):
            deploy_ops.register_task_definition(ecs, family, image)

    output = combined(capsys)
    assert f"ECR image: {image}" in output
    assert "✗ Error registering task definition for 'MyTask': [StatusCode: 404]" in output


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
    context = DeploymentContext(**responses)

    with context.setup_mock() as (function, ecs):
        with pytest.raises(typer.Abort):
            deploy_ops.register_task_definition(ecs, "MyTask", "MyNewImage")


def test_update_ecs_service_succeeds(capsys: pytest.CaptureFixture[str]):
    context = DeploymentContext(update_ecs_responses=[{"ResponseMetadata": {"HTTPStatusCode": 200}}])

    with context.setup_mock() as (function, ecs):
        deploy_ops.update_ecs_service(ecs, "MyTask", "MyService", "MyCluster")

    context.assert_ecs_called("update_service", cluster="MyCluster", service="MyService", taskDefinition="MyTask")
    assert "✓ ECS service 'MyService' has been updated" in combined(capsys)


def test_update_ecs_service_fails(capsys: pytest.CaptureFixture[str]):
    context = DeploymentContext(update_ecs_responses=[{"ResponseMetadata": {"HTTPStatusCode": 404}}])

    with context.setup_mock() as (function, ecs):
        with pytest.raises(typer.Abort):
            deploy_ops.update_ecs_service(ecs, "MyTask", "MyService", "MyCluster")

    context.assert_ecs_called("update_service", cluster="MyCluster", service="MyService", taskDefinition="MyTask")
    assert "✗ Error updating ECS service 'MyService'. StatusCode: 404" in combined(capsys)


def test_find_service_deployment_arn_found_on_first_attempt(mock_time: mock.MagicMock):
    context = DeploymentContext(
        list_deployments_responses=[deployments_response(service_deployment(serviceDeploymentArn="MyDeploymentArn"))]
    )

    with context.setup_mock() as (function, ecs):
        result = deploy_ops.find_service_deployment_arn(ecs, cluster="MyCluster", service="MyService")

    assert result == "MyDeploymentArn"
    context.assert_ecs_called(
        "list_service_deployments", service="MyService", cluster="MyCluster", status=["PENDING", "IN_PROGRESS"]
    )
    mock_time.sleep.assert_not_called()


def test_find_service_deployment_arn_found_after_retries(mock_time: mock.MagicMock):
    context = DeploymentContext(
        list_deployments_responses=[
            deployments_response(),
            deployments_response(service_deployment()),
            deployments_response(service_deployment(serviceDeploymentArn="MyDeploymentArn")),
        ]
    )

    with context.setup_mock() as (function, ecs):
        result = deploy_ops.find_service_deployment_arn(ecs, cluster="MyCluster", service="MyService")

    assert result == "MyDeploymentArn"
    context.assert_ecs_called(
        "list_service_deployments", n=3, service="MyService", cluster="MyCluster", status=["PENDING", "IN_PROGRESS"]
    )
    assert mock_time.sleep.call_count == 2
    mock_time.sleep.assert_called_with(5)


def test_find_service_deployment_arn_aborts_when_never_found(
    mock_time: mock.MagicMock, capsys: pytest.CaptureFixture[str]
):
    context = DeploymentContext(list_deployments_responses=[deployments_response()] * 6)

    with context.setup_mock() as (function, ecs):
        with pytest.raises(typer.Abort):
            deploy_ops.find_service_deployment_arn(ecs, cluster="MyCluster", service="MyService")

    context.assert_ecs_called(
        "list_service_deployments", n=6, service="MyService", cluster="MyCluster", status=["PENDING", "IN_PROGRESS"]
    )
    assert mock_time.sleep.call_count == 5
    assert "✗ No active deployment found for ECS service 'MyService' after updating it" in combined(capsys)


def test_describe_service_deployment_returns_first_deployment():
    context = DeploymentContext(
        describe_deployments_responses=[
            deployments_response(service_deployment(status="IN_PROGRESS"), service_deployment(status="SUCCESSFUL"))
        ]
    )

    with context.setup_mock() as (function, ecs):
        result = deploy_ops.describe_service_deployment(ecs, "MyDeploymentArn")

    assert result == {"status": "IN_PROGRESS"}
    context.assert_ecs_called("describe_service_deployments", serviceDeploymentArns=["MyDeploymentArn"])


def test_describe_service_deployment_aborts_without_deployments(capsys: pytest.CaptureFixture[str]):
    context = DeploymentContext(describe_deployments_responses=[deployments_response()])

    with context.setup_mock() as (function, ecs):
        with pytest.raises(typer.Abort):
            deploy_ops.describe_service_deployment(ecs, "MyDeploymentArn")

    assert "✗ Failed to describe the service deployment 'MyDeploymentArn'" in combined(capsys)


@pytest.mark.parametrize(
    "deployment,expected",
    [
        (
            service_deployment(
                targetServiceRevision={"runningTaskCount": 2, "pendingTaskCount": 1, "requestedTaskCount": 3}
            ),
            "tasks 2/3 running, 1 pending",
        ),
        (service_deployment(), None),
        (service_deployment(targetServiceRevision={"pendingTaskCount": 1, "requestedTaskCount": 3}), None),
        (service_deployment(targetServiceRevision={"runningTaskCount": 2, "requestedTaskCount": 3}), None),
        (service_deployment(targetServiceRevision={"runningTaskCount": 2, "pendingTaskCount": 1}), None),
    ],
    ids=["all_counts", "missing_target_revision", "missing_running", "missing_pending", "missing_requested"],
)
def test_format_task_counts(deployment: ServiceDeploymentTypeDef, expected: str | None):
    assert deploy_ops.format_task_counts(deployment) == expected


@pytest.mark.parametrize(
    "deployment,expected",
    [
        (service_deployment(status="IN_PROGRESS"), "Deployment [bold]IN_PROGRESS[/]"),
        (
            service_deployment(status="IN_PROGRESS", lifecycleStage="DEPLOY_SERVICE"),
            "Deployment [bold]IN_PROGRESS[/] · stage [bold]DEPLOY_SERVICE[/]",
        ),
        (
            service_deployment(
                status="IN_PROGRESS",
                targetServiceRevision={"runningTaskCount": 2, "pendingTaskCount": 1, "requestedTaskCount": 3},
            ),
            "Deployment [bold]IN_PROGRESS[/] · tasks 2/3 running, 1 pending",
        ),
        (
            service_deployment(
                status="IN_PROGRESS",
                lifecycleStage="BAKE_TIME",
                targetServiceRevision={"runningTaskCount": 2, "pendingTaskCount": 1, "requestedTaskCount": 3},
            ),
            "Deployment [bold]IN_PROGRESS[/] · stage [bold]BAKE_TIME[/] · tasks 2/3 running, 1 pending",
        ),
        (service_deployment(), "Deployment [bold]UNKNOWN[/]"),
    ],
    ids=["status_only", "with_stage", "with_task_counts", "with_stage_and_counts", "missing_status"],
)
def test_format_deployment_progress(deployment: ServiceDeploymentTypeDef, expected: str):
    assert deploy_ops.format_deployment_progress(deployment) == expected


def test_deployment_reached_terminal_state_reports_success(capsys: pytest.CaptureFixture[str]):
    result = deploy_ops.deployment_reached_terminal_state(service_deployment(status="SUCCESSFUL"), "MyService")

    assert result is True
    assert "✓ ECS service 'MyService' successfully deployed!" in combined(capsys)


@pytest.mark.parametrize(
    "status", ["PENDING", "IN_PROGRESS", "STOP_REQUESTED", "ROLLBACK_REQUESTED", "ROLLBACK_IN_PROGRESS"]
)
def test_deployment_reached_terminal_state_keeps_polling_on_transient_status(status: str):
    assert deploy_ops.deployment_reached_terminal_state(service_deployment(status=status), "MyService") is False


@pytest.mark.parametrize("reason", ["Circuit breaker", None], ids=["with_reason", "without_reason"])
@pytest.mark.parametrize("status", ["ROLLBACK_SUCCESSFUL", "ROLLBACK_FAILED", "STOPPED"])
def test_deployment_reached_terminal_state_aborts_on_failed_status(
    status: str, reason: str | None, capsys: pytest.CaptureFixture[str]
):
    deployment = (
        service_deployment(status=status) if reason is None else service_deployment(status=status, statusReason=reason)
    )

    with pytest.raises(typer.Abort):
        deploy_ops.deployment_reached_terminal_state(deployment, "MyService")

    expected = f"Deployment of ECS service 'MyService' ended as {status}"
    if reason is not None:
        expected = f"{expected}: {reason}"
    assert f"✗ {expected}" in combined(capsys)


def test_deployment_reached_terminal_state_aborts_without_status(capsys: pytest.CaptureFixture[str]):
    with pytest.raises(typer.Abort):
        deploy_ops.deployment_reached_terminal_state(service_deployment(), "MyService")

    assert "✗ Failed to get the status of the deployment for ECS service 'MyService'" in combined(capsys)


def test_waiting_for_deployment_succeeds_and_suppresses_duplicate_progress(
    mock_time: mock.MagicMock, capsys: pytest.CaptureFixture[str]
):
    context = DeploymentContext(
        list_deployments_responses=[deployments_response(service_deployment(serviceDeploymentArn="MyDeploymentArn"))],
        describe_deployments_responses=[
            deployments_response(service_deployment(status="IN_PROGRESS")),
            deployments_response(service_deployment(status="IN_PROGRESS")),
            deployments_response(service_deployment(status="SUCCESSFUL")),
        ],
    )

    with context.setup_mock() as (function, ecs):
        deploy_ops.waiting_for_deployment_to_finish(ecs, cluster="MyCluster", service="MyService")

    context.assert_ecs_called("describe_service_deployments", n=3, serviceDeploymentArns=["MyDeploymentArn"])
    output = combined(capsys)
    # The two identical IN_PROGRESS polls collapse into a single progress line.
    assert output.count("Deployment IN_PROGRESS") == 1
    assert "✓ ECS service 'MyService' successfully deployed!" in output
    assert mock_time.sleep.call_count == 2
    mock_time.sleep.assert_called_with(10)


def test_waiting_for_deployment_prints_progress_on_each_transition(capsys: pytest.CaptureFixture[str]):
    context = DeploymentContext(
        list_deployments_responses=[deployments_response(service_deployment(serviceDeploymentArn="MyDeploymentArn"))],
        describe_deployments_responses=[
            deployments_response(service_deployment(status="IN_PROGRESS", lifecycleStage="DEPLOY_SERVICE")),
            deployments_response(service_deployment(status="IN_PROGRESS", lifecycleStage="BAKE_TIME")),
            deployments_response(service_deployment(status="SUCCESSFUL")),
        ],
    )

    with context.setup_mock() as (function, ecs):
        deploy_ops.waiting_for_deployment_to_finish(ecs, cluster="MyCluster", service="MyService")

    output = combined(capsys)
    assert "Deployment IN_PROGRESS · stage DEPLOY_SERVICE" in output
    assert "Deployment IN_PROGRESS · stage BAKE_TIME" in output
    assert "Deployment SUCCESSFUL" in output


def test_waiting_for_deployment_prints_heartbeat_when_progress_is_unchanged(
    mock_time: mock.MagicMock, capsys: pytest.CaptureFixture[str]
):
    context = DeploymentContext(
        list_deployments_responses=[deployments_response(service_deployment(serviceDeploymentArn="MyDeploymentArn"))],
        describe_deployments_responses=[
            deployments_response(service_deployment(status="IN_PROGRESS")),
            deployments_response(service_deployment(status="IN_PROGRESS")),
            deployments_response(service_deployment(status="SUCCESSFUL")),
        ],
    )
    mock_time.monotonic.side_effect = [0.0, 61.0, 61.0]

    with context.setup_mock() as (function, ecs):
        deploy_ops.waiting_for_deployment_to_finish(ecs, cluster="MyCluster", service="MyService")

    assert combined(capsys).count("Deployment IN_PROGRESS") == 2


def test_waiting_for_deployment_polls_through_rollback_and_aborts_on_final_outcome(capsys: pytest.CaptureFixture[str]):
    context = DeploymentContext(
        list_deployments_responses=[deployments_response(service_deployment(serviceDeploymentArn="MyDeploymentArn"))],
        describe_deployments_responses=[
            deployments_response(service_deployment(status="IN_PROGRESS")),
            deployments_response(service_deployment(status="ROLLBACK_IN_PROGRESS")),
            deployments_response(service_deployment(status="ROLLBACK_SUCCESSFUL", statusReason="Circuit breaker")),
        ],
    )

    with context.setup_mock() as (function, ecs):
        with pytest.raises(typer.Abort):
            deploy_ops.waiting_for_deployment_to_finish(ecs, cluster="MyCluster", service="MyService")

    context.assert_ecs_called("describe_service_deployments", n=3, serviceDeploymentArns=["MyDeploymentArn"])
    output = combined(capsys)
    assert "Deployment ROLLBACK_IN_PROGRESS" in output
    assert "✗ Deployment of ECS service 'MyService' ended as ROLLBACK_SUCCESSFUL: Circuit breaker" in output


def test_command_deploys_each_service_with_its_registered_task_definition():
    # The recurrent-events service rolls onto its own events image, never the bot image.
    with (
        mock.patch("mb.deploy_ops.update_lambda_code") as update_lambda_code,
        mock.patch("mb.deploy_ops.invoke_lambda") as invoke_lambda,
        mock.patch("mb.deploy_ops.register_task_definition") as register_task_definition,
        mock.patch("mb.deploy_ops.update_ecs_service") as update_ecs_service,
        mock.patch("mb.deploy_ops.waiting_for_deployment_to_finish") as waiting_for_deployment_to_finish,
    ):
        register_task_definition.side_effect = ["MitupTaskArn", "RecurrentEventsTaskArn"]

        manager = mock.MagicMock()
        manager.attach_mock(update_lambda_code, "update_lambda_code")
        manager.attach_mock(invoke_lambda, "invoke_lambda")
        manager.attach_mock(register_task_definition, "register_task_definition")
        manager.attach_mock(update_ecs_service, "update_ecs_service")
        manager.attach_mock(waiting_for_deployment_to_finish, "waiting_for_deployment_to_finish")

        with DeploymentContext().setup_mock() as (function, ecs):
            result = cli.invoke(
                app,
                [
                    "deploy",
                    "--migrations-image",
                    "migrations_image:latest",
                    "--bot-image",
                    "bot_image:latest",
                    "--alarm-action-image",
                    "alarm_action_image:latest",
                    "--events-image",
                    "events_image:latest",
                ],
            )

        assert result.exit_code == 0, result.output
        assert manager.mock_calls == [
            mock.call.update_lambda_code(function, "MitupMigrationsLambda", "migrations_image:latest"),
            mock.call.invoke_lambda(function, "MitupMigrationsLambda"),
            mock.call.update_lambda_code(function, "MitupAlarmActionLambda", "alarm_action_image:latest"),
            mock.call.register_task_definition(ecs, "mitup", "bot_image:latest"),
            mock.call.update_ecs_service(ecs, "MitupTaskArn", service="mitup", cluster="mitup"),
            mock.call.waiting_for_deployment_to_finish(ecs, cluster="mitup", service="mitup"),
            mock.call.register_task_definition(ecs, "mitup-recurrent-events", "events_image:latest"),
            mock.call.update_ecs_service(
                ecs, "RecurrentEventsTaskArn", service="mitup-recurrent-events", cluster="mitup-recurrent-events"
            ),
            mock.call.waiting_for_deployment_to_finish(
                ecs, cluster="mitup-recurrent-events", service="mitup-recurrent-events"
            ),
        ]


@pytest.mark.parametrize(
    "side_effects,number_of_calls,exit_code",
    [
        [(typer.Abort, None, [None, None], [None, None], [None, None]), (1, 0, 0, 0, 0), 1],
        [(None, typer.Abort, [None, None], [None, None], [None, None]), (1, 1, 0, 0, 0), 1],
        [(None, None, [typer.Abort, None], [None, None], [None, None]), (2, 1, 1, 0, 0), 1],
        [(None, None, [None, None], [typer.Abort, None], [None, None]), (2, 1, 1, 1, 0), 1],
        [(None, None, [None, None], [None, None], [typer.Abort, None]), (2, 1, 1, 1, 1), 1],
        [(None, None, [None, typer.Abort], [None, None], [None, None]), (2, 1, 2, 1, 1), 1],
        [(None, None, [None, None], [None, typer.Abort], [None, None]), (2, 1, 2, 2, 1), 1],
        [(None, None, [None, None], [None, None], [None, typer.Abort]), (2, 1, 2, 2, 2), 1],
        [(None, None, [None, None], [None, None], [None, None]), (2, 1, 2, 2, 2), 0],
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
        "all_successful",
    ],
)
def test_command_chain_is_not_broken(
    side_effects: tuple[type[typer.Abort] | None, ...], number_of_calls: tuple[int, ...], exit_code: int
):
    with (
        mock.patch("mb.deploy_ops.update_lambda_code") as update_lambda_code,
        mock.patch("mb.deploy_ops.invoke_lambda") as invoke_lambda,
        mock.patch("mb.deploy_ops.register_task_definition") as register_task_definition,
        mock.patch("mb.deploy_ops.update_ecs_service") as update_ecs_service,
        mock.patch("mb.deploy_ops.waiting_for_deployment_to_finish") as waiting_for_deployment_to_finish,
    ):
        update_lambda_code.side_effect = side_effects[0]
        invoke_lambda.side_effect = side_effects[1]
        register_task_definition.side_effect = side_effects[2]
        update_ecs_service.side_effect = side_effects[3]
        waiting_for_deployment_to_finish.side_effect = side_effects[4]

        with DeploymentContext().setup_mock() as (func, ecs):
            result = cli.invoke(
                app,
                [
                    "deploy",
                    "--migrations-image",
                    "migrations_image:latest",
                    "--bot-image",
                    "bot_image:latest",
                    "--alarm-action-image",
                    "alarm_action_image:latest",
                    # Supply an events image so the abort chain covers both ECS services.
                    "--events-image",
                    "events_image:latest",
                ],
            )

        assert result.exit_code == exit_code, result.output
        assert len(update_lambda_code.call_args_list) == number_of_calls[0]
        assert len(invoke_lambda.call_args_list) == number_of_calls[1]
        assert len(register_task_definition.call_args_list) == number_of_calls[2]
        assert len(update_ecs_service.call_args_list) == number_of_calls[3]
        assert len(waiting_for_deployment_to_finish.call_args_list) == number_of_calls[4]
