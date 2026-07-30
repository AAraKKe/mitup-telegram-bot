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
from mypy_boto3_ecs.type_defs import ContainerDefinitionOutputTypeDef, ServiceDeploymentTypeDef
from mypy_boto3_lambda import LambdaClient
from rich.text import Text
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
    # A sidecar ahead of the app container mirrors the real task definitions, where an init
    # container sits at index 0 and only the container named after the family takes the new image.
    container_definitions = [
        {"name": "init", "image": "InitImage"},
        {"name": family, "image": "SomePreviousImage"},
    ]
    volumes = [{"name": "shared-volume", "host": {}}]

    context = DeploymentContext(
        describe_task_responses=[
            {
                "taskDefinition": {
                    "family": family,
                    "containerDefinitions": container_definitions,
                    "executionRoleArn": role,
                    "taskRoleArn": "SomeTaskRoleArn",
                    "networkMode": "bridge",
                    "volumes": volumes,
                    # Response-only metadata that RegisterTaskDefinition rejects:
                    "taskDefinitionArn": task_arn,
                    "revision": 19,
                    "status": "ACTIVE",
                    "requiresAttributes": [{"name": "ecs.capability.task-iam-role"}],
                    "compatibilities": ["EC2"],
                    "registeredAt": "2026-01-01T00:00:00Z",
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
    # The definition passes through wholesale — volumes, roles and network mode included, the
    # response-only metadata stripped — with the new image and the release marker naming it only on
    # the app container, never on a sidecar.
    context.assert_ecs_called(
        "register_task_definition",
        family=family,
        containerDefinitions=[
            {"name": "init", "image": "InitImage"},
            {
                "name": family,
                "image": image,
                "environment": [{"name": deploy_ops.RELEASE_ENV_VAR, "value": image}],
            },
        ],
        executionRoleArn=role,
        taskRoleArn="SomeTaskRoleArn",
        networkMode="bridge",
        volumes=volumes,
    )
    assert result == task_arn
    output = combined(capsys)
    assert f"ECR image: {image}" in output
    assert "✓ New task definition defined for family 'MyTask', revision: 20" in output


def test_release_marker_is_the_image_tag():
    """The tag alone: the repository URI in front of it names the registry and the AWS account, and
    the app stamps this value on every line it writes."""
    assert deploy_ops.release_marker("123456789012.dkr.ecr.eu-west-1.amazonaws.com/mitup:ci-9f3a1c2") == "ci-9f3a1c2"


def test_release_marker_replaces_the_previous_one_and_keeps_the_other_variables():
    """The definition is re-registered from the described revision, so the marker from the last
    deploy is already on it — appending without dropping it would leave two entries for one name."""
    container: ContainerDefinitionOutputTypeDef = {
        "name": "mitup",
        "environment": [
            {"name": "MITUPBOT__DB__POOL_SIZE", "value": "10"},
            {"name": deploy_ops.RELEASE_ENV_VAR, "value": "ci-previous"},
        ],
    }

    deploy_ops.set_release_marker(container, "repo/mitup:ci-9f3a1c2")

    assert container["environment"] == [
        {"name": "MITUPBOT__DB__POOL_SIZE", "value": "10"},
        {"name": deploy_ops.RELEASE_ENV_VAR, "value": "ci-9f3a1c2"},
    ]


def test_register_task_definition_when_failing(capsys: pytest.CaptureFixture[str]):
    family, image, role, task_arn = "MyTask", "MyNewImage", "SomeRoleArn", "MyTaskArn"
    container_definitions = [{"name": family, "image": "SomePreviousImage"}]

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


def task_definition_response_factory(container_definitions=True, execution_role=True, container_name="MyTask"):
    response: dict[str, dict[str, Any]] = {"taskDefinition": {"family": "MyTask"}}
    if container_definitions:
        response["taskDefinition"]["containerDefinitions"] = [{"name": container_name, "image": "MyNewImage"}]
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
            "describe_task_responses": [task_definition_response_factory(container_name="sidecar-only")],
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
        "missing_app_container",
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

    context.assert_ecs_called(
        "update_service", cluster="MyCluster", service="MyService", taskDefinition="MyTask", forceNewDeployment=False
    )
    assert "✓ ECS service 'MyService' has been updated" in combined(capsys)


def test_update_ecs_service_fails(capsys: pytest.CaptureFixture[str]):
    context = DeploymentContext(update_ecs_responses=[{"ResponseMetadata": {"HTTPStatusCode": 404}}])

    with context.setup_mock() as (function, ecs):
        with pytest.raises(typer.Abort):
            deploy_ops.update_ecs_service(ecs, "MyTask", "MyService", "MyCluster")

    context.assert_ecs_called(
        "update_service", cluster="MyCluster", service="MyService", taskDefinition="MyTask", forceNewDeployment=False
    )
    assert "✗ Error updating ECS service 'MyService'. StatusCode: 404" in combined(capsys)


def test_update_ecs_service_forces_new_deployment(capsys: pytest.CaptureFixture[str]):
    context = DeploymentContext(update_ecs_responses=[{"ResponseMetadata": {"HTTPStatusCode": 200}}])

    with context.setup_mock() as (function, ecs):
        deploy_ops.update_ecs_service(ecs, "MyTask", "MyService", "MyCluster", force_new_deployment=True)

    context.assert_ecs_called(
        "update_service", cluster="MyCluster", service="MyService", taskDefinition="MyTask", forceNewDeployment=True
    )
    assert "✓ ECS service 'MyService' has been updated" in combined(capsys)


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


def plain_line(markup_line: str) -> str:
    return Text.from_markup(markup_line).plain


def aligned_columns(service: str, name_width: int, status: str, stage: str, task_counts: str) -> str:
    return " · ".join(
        (
            service.ljust(name_width),
            status.ljust(deploy_ops.STATUS_COLUMN_WIDTH),
            stage.ljust(deploy_ops.STAGE_COLUMN_WIDTH),
            task_counts.ljust(deploy_ops.TASK_COUNTS_COLUMN_WIDTH),
        )
    )


@pytest.mark.parametrize(
    "deployment,status,stage,task_counts",
    [
        (
            service_deployment(
                status="IN_PROGRESS",
                lifecycleStage="BAKE_TIME",
                targetServiceRevision={"runningTaskCount": 2, "pendingTaskCount": 1, "requestedTaskCount": 3},
            ),
            "IN PROGRESS",
            "BAKE TIME",
            "tasks 2/3 running, 1 pending",
        ),
        (service_deployment(status="IN_PROGRESS"), "IN PROGRESS", "—", "—"),
        (service_deployment(), "UNKNOWN", "—", "—"),
    ],
    ids=["all_columns", "missing_stage_and_counts", "missing_status"],
)
def test_format_deployment_line_pads_every_column(
    deployment: ServiceDeploymentTypeDef, status: str, stage: str, task_counts: str
):
    line = deploy_ops.format_deployment_line(deployment, service="mitup", name_width=22, service_style="bold cyan")

    assert plain_line(line) == aligned_columns("mitup", 22, status, stage, task_counts)


def test_format_deployment_line_styles_service_status_and_stage():
    deployment = service_deployment(status="IN_PROGRESS", lifecycleStage="BAKE_TIME")

    line = deploy_ops.format_deployment_line(deployment, service="mitup", name_width=5, service_style="bold cyan")

    assert line.startswith("[bold cyan]mitup[/]")
    assert "[yellow]" in line
    assert "[bold bright_blue]" in line


@pytest.mark.parametrize(
    "seconds,expected",
    [(0.0, "00:00"), (59.9, "00:59"), (61.0, "01:01"), (3700.0, "61:40")],
    ids=["zero", "sub_minute", "over_a_minute", "over_an_hour"],
)
def test_format_stage_elapsed(seconds: float, expected: str):
    assert deploy_ops.format_stage_elapsed(seconds) == expected


def test_progress_log_assigns_distinct_service_styles_and_pads_to_the_longest_name():
    progress_log = deploy_ops.DeploymentProgressLog.for_services(["mitup", "mitup-recurrent-events"])

    assert progress_log.service_styles["mitup"] != progress_log.service_styles["mitup-recurrent-events"]
    assert progress_log.name_width == len("mitup-recurrent-events")


def test_progress_log_suppresses_unchanged_lines(capsys: pytest.CaptureFixture[str]):
    progress_log = deploy_ops.DeploymentProgressLog.for_services(["mitup"])
    deployment = service_deployment(status="IN_PROGRESS")

    progress_log.echo("mitup", deployment, 0.0, heartbeat=False)
    progress_log.echo("mitup", deployment, 10.0, heartbeat=False)

    assert combined(capsys).count("IN PROGRESS") == 1


def test_progress_log_timer_counts_within_a_stage_and_resets_on_stage_change(capsys: pytest.CaptureFixture[str]):
    progress_log = deploy_ops.DeploymentProgressLog.for_services(["mitup"])
    baking = service_deployment(status="IN_PROGRESS", lifecycleStage="BAKE_TIME")
    cleaning = service_deployment(status="IN_PROGRESS", lifecycleStage="CLEAN_UP")

    progress_log.echo("mitup", baking, 0.0, heartbeat=False)
    progress_log.echo("mitup", baking, 70.0, heartbeat=True)
    progress_log.echo("mitup", cleaning, 100.0, heartbeat=False)

    timers = [line.rsplit(" · ", 1)[1] for line in combined(capsys).splitlines()]
    assert timers == ["00:00", "01:10", "00:00"]


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


def test_wait_for_deployments_succeeds_and_suppresses_duplicate_progress(
    mock_time: mock.MagicMock, capsys: pytest.CaptureFixture[str]
):
    context = DeploymentContext(
        describe_deployments_responses=[
            deployments_response(service_deployment(status="IN_PROGRESS")),
            deployments_response(service_deployment(status="IN_PROGRESS")),
            deployments_response(service_deployment(status="SUCCESSFUL")),
        ],
    )

    with context.setup_mock() as (function, ecs):
        deploy_ops.wait_for_deployments(ecs, {"MyService": "MyDeploymentArn"})

    context.assert_ecs_called("describe_service_deployments", n=3, serviceDeploymentArns=["MyDeploymentArn"])
    context.ecs_client.list_service_deployments.assert_not_called()
    output = combined(capsys)
    # The two identical IN_PROGRESS polls collapse into a single progress line.
    assert output.count("IN PROGRESS") == 1
    assert "✓ ECS service 'MyService' successfully deployed!" in output
    assert mock_time.sleep.call_count == 2
    mock_time.sleep.assert_called_with(10)


def test_wait_for_deployments_prints_progress_on_each_transition(capsys: pytest.CaptureFixture[str]):
    context = DeploymentContext(
        describe_deployments_responses=[
            deployments_response(service_deployment(status="IN_PROGRESS", lifecycleStage="SCALE_UP")),
            deployments_response(service_deployment(status="IN_PROGRESS", lifecycleStage="BAKE_TIME")),
            deployments_response(service_deployment(status="SUCCESSFUL")),
        ],
    )

    with context.setup_mock() as (function, ecs):
        deploy_ops.wait_for_deployments(ecs, {"MyService": "MyDeploymentArn"})

    output = combined(capsys)
    assert "SCALE UP" in output
    assert "BAKE TIME" in output
    assert "SUCCESSFUL" in output


def test_wait_for_deployments_prints_heartbeat_when_progress_is_unchanged(
    mock_time: mock.MagicMock, capsys: pytest.CaptureFixture[str]
):
    context = DeploymentContext(
        describe_deployments_responses=[
            deployments_response(service_deployment(status="IN_PROGRESS")),
            deployments_response(service_deployment(status="IN_PROGRESS")),
            deployments_response(service_deployment(status="SUCCESSFUL")),
        ],
    )
    mock_time.monotonic.side_effect = [0.0, 61.0, 61.0]

    with context.setup_mock() as (function, ecs):
        deploy_ops.wait_for_deployments(ecs, {"MyService": "MyDeploymentArn"})

    assert combined(capsys).count("IN PROGRESS") == 2


def test_wait_for_deployments_polls_through_rollback_and_aborts_on_final_outcome(capsys: pytest.CaptureFixture[str]):
    context = DeploymentContext(
        describe_deployments_responses=[
            deployments_response(service_deployment(status="IN_PROGRESS")),
            deployments_response(service_deployment(status="ROLLBACK_IN_PROGRESS")),
            deployments_response(service_deployment(status="ROLLBACK_SUCCESSFUL", statusReason="Circuit breaker")),
        ],
    )

    with context.setup_mock() as (function, ecs):
        with pytest.raises(typer.Abort):
            deploy_ops.wait_for_deployments(ecs, {"MyService": "MyDeploymentArn"})

    context.assert_ecs_called("describe_service_deployments", n=3, serviceDeploymentArns=["MyDeploymentArn"])
    output = combined(capsys)
    assert "ROLLBACK IN PROGRESS" in output
    assert "✗ Deployment of ECS service 'MyService' ended as ROLLBACK_SUCCESSFUL: Circuit breaker" in output


def test_wait_for_deployments_polls_every_service_each_round(capsys: pytest.CaptureFixture[str]):
    # mitup succeeds first; events keeps being polled until it succeeds a round later.
    context = DeploymentContext(
        describe_deployments_responses=[
            deployments_response(service_deployment(status="IN_PROGRESS")),  # round 1: mitup
            deployments_response(service_deployment(status="IN_PROGRESS")),  # round 1: events
            deployments_response(service_deployment(status="SUCCESSFUL")),  # round 2: mitup
            deployments_response(service_deployment(status="IN_PROGRESS")),  # round 2: events
            deployments_response(service_deployment(status="SUCCESSFUL")),  # round 3: events only
        ],
    )

    with context.setup_mock() as (function, ecs):
        deploy_ops.wait_for_deployments(ecs, {"mitup": "MitupArn", "mitup-recurrent-events": "EventsArn"})

    # 2 + 2 + 1 polls; the last round only re-polls events, which is still in flight after mitup ends.
    context.assert_ecs_called("describe_service_deployments", n=5, serviceDeploymentArns=["EventsArn"])
    output = combined(capsys)
    # Both service names pad to the longest name, so every column starts at the same offset.
    mitup_line = "mitup".ljust(len("mitup-recurrent-events")) + " · IN PROGRESS"
    events_line = "mitup-recurrent-events · IN PROGRESS"
    assert mitup_line in output
    assert events_line in output
    assert "✓ ECS service 'mitup' successfully deployed!" in output
    assert "✓ ECS service 'mitup-recurrent-events' successfully deployed!" in output
    # Events progress is visible before mitup finishes — the whole point of polling both together.
    assert output.index(events_line) < output.index("✓ ECS service 'mitup' successfully deployed!")


def test_start_ecs_deployment_registers_updates_and_returns_arn():
    with (
        mock.patch("mb.deploy_ops.register_task_definition") as register_task_definition,
        mock.patch("mb.deploy_ops.update_ecs_service") as update_ecs_service,
        mock.patch("mb.deploy_ops.find_service_deployment_arn") as find_service_deployment_arn,
    ):
        register_task_definition.return_value = "MyTaskArn"
        find_service_deployment_arn.return_value = "MyDeploymentArn"
        ecs = mock.MagicMock(spec=ECSClient)

        result = deploy_ops.start_ecs_deployment(ecs, "mitup", "bot_image:latest")

    assert result == "MyDeploymentArn"
    register_task_definition.assert_called_once_with(ecs, "mitup", "bot_image:latest")
    update_ecs_service.assert_called_once_with(ecs, "MyTaskArn", service="mitup", cluster="mitup")
    find_service_deployment_arn.assert_called_once_with(ecs, cluster="mitup", service="mitup")


def test_start_ecs_refresh_forces_new_deployment_without_registering():
    with (
        mock.patch("mb.deploy_ops.register_task_definition") as register_task_definition,
        mock.patch("mb.deploy_ops.update_ecs_service") as update_ecs_service,
        mock.patch("mb.deploy_ops.find_service_deployment_arn") as find_service_deployment_arn,
    ):
        find_service_deployment_arn.return_value = "MyDeploymentArn"
        ecs = mock.MagicMock(spec=ECSClient)

        result = deploy_ops.start_ecs_refresh(ecs, "mitup")

    assert result == "MyDeploymentArn"
    register_task_definition.assert_not_called()
    # The family name equals the service name; ECS resolves it to the latest ACTIVE revision.
    update_ecs_service.assert_called_once_with(
        ecs, "mitup", service="mitup", cluster="mitup", force_new_deployment=True
    )
    find_service_deployment_arn.assert_called_once_with(ecs, cluster="mitup", service="mitup")


def test_command_starts_both_services_before_waiting():
    # The recurrent-events service rolls onto its own events image, never the bot image. Both roll-outs
    # start before either wait so their bake windows overlap for the cross-gated rollback alarms.
    with (
        mock.patch("mb.deploy_ops.update_lambda_code") as update_lambda_code,
        mock.patch("mb.deploy_ops.invoke_lambda") as invoke_lambda,
        mock.patch("mb.deploy_ops.start_ecs_deployment") as start_ecs_deployment,
        mock.patch("mb.deploy_ops.wait_for_deployments") as wait_for_deployments,
    ):
        start_ecs_deployment.side_effect = ["MitupDeploymentArn", "RecurrentEventsDeploymentArn"]

        manager = mock.MagicMock()
        manager.attach_mock(update_lambda_code, "update_lambda_code")
        manager.attach_mock(invoke_lambda, "invoke_lambda")
        manager.attach_mock(start_ecs_deployment, "start_ecs_deployment")
        manager.attach_mock(wait_for_deployments, "wait_for_deployments")

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
            mock.call.start_ecs_deployment(ecs, "mitup", "bot_image:latest"),
            mock.call.start_ecs_deployment(ecs, "mitup-recurrent-events", "events_image:latest"),
            mock.call.wait_for_deployments(
                ecs, {"mitup": "MitupDeploymentArn", "mitup-recurrent-events": "RecurrentEventsDeploymentArn"}
            ),
        ]


def test_command_refresh_starts_both_services_before_waiting():
    with (
        mock.patch("mb.deploy_ops.start_ecs_refresh") as start_ecs_refresh,
        mock.patch("mb.deploy_ops.wait_for_deployments") as wait_for_deployments,
    ):
        start_ecs_refresh.side_effect = ["MitupDeploymentArn", "RecurrentEventsDeploymentArn"]

        manager = mock.MagicMock()
        manager.attach_mock(start_ecs_refresh, "start_ecs_refresh")
        manager.attach_mock(wait_for_deployments, "wait_for_deployments")

        with DeploymentContext().setup_mock() as (function, ecs):
            result = cli.invoke(app, ["deploy", "--refresh"])

        assert result.exit_code == 0, result.output
        assert manager.mock_calls == [
            mock.call.start_ecs_refresh(ecs, "mitup"),
            mock.call.start_ecs_refresh(ecs, "mitup-recurrent-events"),
            mock.call.wait_for_deployments(
                ecs, {"mitup": "MitupDeploymentArn", "mitup-recurrent-events": "RecurrentEventsDeploymentArn"}
            ),
        ]


def test_command_refresh_skips_migrations_and_images():
    with (
        mock.patch("mb.deploy_ops.refresh") as refresh,
        mock.patch("mb.deploy_ops.deploy") as deploy,
    ):
        result = cli.invoke(app, ["deploy", "--refresh"])

    assert result.exit_code == 0, result.output
    refresh.assert_called_once_with()
    deploy.assert_not_called()


def test_command_aborts_when_images_missing_without_refresh():
    with (
        mock.patch("mb.deploy_ops.refresh") as refresh,
        mock.patch("mb.deploy_ops.deploy") as deploy,
    ):
        result = cli.invoke(app, ["deploy", "--bot-image", "bot_image:latest"])

    assert result.exit_code != 0
    refresh.assert_not_called()
    deploy.assert_not_called()
    assert "required unless --refresh" in result.output


@pytest.mark.parametrize(
    "side_effects,number_of_calls,exit_code",
    [
        [(typer.Abort, None, [None, None], None), (1, 0, 0, 0), 1],
        [(None, typer.Abort, [None, None], None), (1, 1, 0, 0), 1],
        [(None, None, [typer.Abort, None], None), (2, 1, 1, 0), 1],
        [(None, None, [None, typer.Abort], None), (2, 1, 2, 0), 1],
        [(None, None, [None, None], typer.Abort), (2, 1, 2, 1), 1],
        [(None, None, [None, None], None), (2, 1, 2, 1), 0],
    ],
    ids=[
        "fail_on_update_migrations_lambda",
        "fail_on_invoke_lambda",
        "fail_on_start_bot_deployment",
        "fail_on_start_events_deployment",
        "fail_on_wait_for_deployments",
        "all_successful",
    ],
)
def test_command_chain_is_not_broken(
    side_effects: tuple[type[typer.Abort] | None, ...], number_of_calls: tuple[int, ...], exit_code: int
):
    with (
        mock.patch("mb.deploy_ops.update_lambda_code") as update_lambda_code,
        mock.patch("mb.deploy_ops.invoke_lambda") as invoke_lambda,
        mock.patch("mb.deploy_ops.start_ecs_deployment") as start_ecs_deployment,
        mock.patch("mb.deploy_ops.wait_for_deployments") as wait_for_deployments,
    ):
        update_lambda_code.side_effect = side_effects[0]
        invoke_lambda.side_effect = side_effects[1]
        start_ecs_deployment.side_effect = side_effects[2]
        wait_for_deployments.side_effect = side_effects[3]

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
        assert len(start_ecs_deployment.call_args_list) == number_of_calls[2]
        assert len(wait_for_deployments.call_args_list) == number_of_calls[3]
