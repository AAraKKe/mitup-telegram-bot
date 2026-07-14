import base64
import json
import time

import boto3
import typer
from mypy_boto3_ecs import ECSClient
from mypy_boto3_ecs.type_defs import ServiceDeploymentTypeDef
from mypy_boto3_lambda import LambdaClient
from mypy_boto3_lambda.type_defs import FunctionConfigurationTypeDef

from . import console

LAMBDA_POLL_INTERVAL_SECONDS = 5
DEPLOYMENT_POLL_INTERVAL_SECONDS = 10
DEPLOYMENT_HEARTBEAT_SECONDS = 60
DEPLOYMENT_DISCOVERY_ATTEMPTS = 6
DEPLOYMENT_DISCOVERY_DELAY_SECONDS = 5


def wait_for_lambda_update(lambda_client: LambdaClient, name: str) -> FunctionConfigurationTypeDef:
    while True:
        configuration = lambda_client.get_function(FunctionName=name)["Configuration"]
        if (status := configuration.get("LastUpdateStatus")) is None:
            console.error(f"Failed to get the status of the lambda function {name!r}")
            raise typer.Abort()

        if status != "InProgress":
            return configuration

        time.sleep(LAMBDA_POLL_INTERVAL_SECONDS)


def update_lambda_code(lambda_client: LambdaClient, name: str, image: str):
    console.info(f"Updating {name} lambda code...")
    lambda_client.update_function_code(FunctionName=name, ImageUri=image, Publish=True)

    configuration = wait_for_lambda_update(lambda_client, name)
    if configuration.get("LastUpdateStatus") == "Failed":
        console.error(f"Failed updating code for lambda {name} for the following reason:")
        console.info(str(configuration.get("LastUpdateStatusReason")))
        raise typer.Abort()

    console.success(f"Lambda {name} function code updated")


def invoke_lambda(lambda_client: LambdaClient, name: str):
    console.info(f"Invoking lambda function {name}...")
    payload = {"action": "upgrade", "revision": "head"}
    response = lambda_client.invoke(
        FunctionName=name,
        LogType="Tail",
        Payload=json.dumps(payload),
    )

    console.rule("[bold]Log[/bold]")
    result = base64.b64decode(response["LogResult"]).decode().splitlines()
    for line in result:
        if not line:
            # Avoid empty lines that can come with the logs
            continue

        try:
            json_event = json.loads(line)
        except json.JSONDecodeError:
            # Cannot parse as json, i.e. it is just a line
            console.info(line)
        else:
            if "log_level" in json_event and json_event["log_level"] == "ERROR":
                # Error log
                console.info(f"[bold red]Lambda failed execution: {json_event['errorMessage']}[/]")
                console.info("Stack trace:")
                for stack_line in json_event["stackTrace"]:
                    console.info(stack_line)
            if "type" in json_event and json_event["type"] == "platform.report":
                exec_time = json_event["record"]["metrics"]["durationMs"]
                # Report execution, can get information about time
                console.rule(f"[bold]Lambda {name} run in {exec_time} ms")

    if response["StatusCode"] != 200 or "FunctionError" in response:
        function_error = response["FunctionError"]
        console.error(f"Error invoking lambda {name}: {function_error}")
        raise typer.Abort()

    console.success(f"Lambda {name} finished successfully")


def register_task_definition(ecs_client: ECSClient, family: str, image: str) -> str:
    console.info(f"Registering task definition {family!r}...")
    console.info(f"ECR image: {image}")

    task_def = ecs_client.describe_task_definition(taskDefinition=family)
    if (containers_definition := task_def["taskDefinition"].get("containerDefinitions")) is None:
        console.error(f"Task definition {family!r} does not have any container definitions")
        raise typer.Abort()

    containers_definition[0]["image"] = image
    if (execution_role := task_def["taskDefinition"].get("executionRoleArn")) is None:
        console.error(f"Task definition {family!r} does not have an execution role")
        raise typer.Abort()

    response = ecs_client.register_task_definition(
        family=family,
        containerDefinitions=containers_definition,
        executionRoleArn=execution_role,
    )
    if (status_code := response["ResponseMetadata"].get("HTTPStatusCode")) is None:
        console.error(f"Failed to get status code for registering task definition {family!r}")
        raise typer.Abort()

    if status_code != 200:
        console.error(f"Error registering task definition for {family!r}: [StatusCode: {status_code}]")
        raise typer.Abort()

    if (revision := response["taskDefinition"].get("revision")) is None:
        console.error(f"Failed to get revision for task definition {family!r}")
        raise typer.Abort()

    console.success(f"New task definition defined for family {family!r}, revision: {revision}")

    if (arn := response["taskDefinition"].get("taskDefinitionArn")) is None:
        console.error(f"Failed to get task definition ARN for {family!r}")
        raise typer.Abort()

    return arn


def update_ecs_service(
    ecs_client: ECSClient, task_definition: str, service: str, cluster: str, *, force_new_deployment: bool = False
):
    console.info(f"Updating ECS service {service!r}...")
    response = ecs_client.update_service(
        cluster=cluster,
        service=service,
        taskDefinition=task_definition,
        forceNewDeployment=force_new_deployment,
    )

    status_code = response["ResponseMetadata"]["HTTPStatusCode"]
    if status_code != 200:
        console.error(f"Error updating ECS service {service!r}. StatusCode: {status_code}")
        raise typer.Abort()
    console.success(f"ECS service {service!r} has been updated")


def find_service_deployment_arn(ecs_client: ECSClient, cluster: str, service: str) -> str:
    """Find the ARN of the deployment triggered by the last ``update_service`` call.

    CI serializes deploys through the GitLab resource_group, so the newest active
    deployment is always ours. It can take a moment to appear, hence the retries.
    """
    for attempt in range(DEPLOYMENT_DISCOVERY_ATTEMPTS):
        if attempt:
            time.sleep(DEPLOYMENT_DISCOVERY_DELAY_SECONDS)

        response = ecs_client.list_service_deployments(
            service=service, cluster=cluster, status=["PENDING", "IN_PROGRESS"]
        )
        deployments = response["serviceDeployments"]
        if deployments and (arn := deployments[0].get("serviceDeploymentArn")) is not None:
            return arn

    console.error(f"No active deployment found for ECS service {service!r} after updating it")
    raise typer.Abort()


def describe_service_deployment(ecs_client: ECSClient, deployment_arn: str) -> ServiceDeploymentTypeDef:
    response = ecs_client.describe_service_deployments(serviceDeploymentArns=[deployment_arn])
    deployments = response["serviceDeployments"]
    if not deployments:
        console.error(f"Failed to describe the service deployment {deployment_arn!r}")
        raise typer.Abort()

    return deployments[0]


def format_task_counts(deployment: ServiceDeploymentTypeDef) -> str | None:
    if (target_revision := deployment.get("targetServiceRevision")) is None:
        return None

    running = target_revision.get("runningTaskCount")
    pending = target_revision.get("pendingTaskCount")
    requested = target_revision.get("requestedTaskCount")
    if running is None or pending is None or requested is None:
        return None

    return f"tasks {running}/{requested} running, {pending} pending"


def format_deployment_progress(deployment: ServiceDeploymentTypeDef) -> str:
    parts = [f"Deployment [bold]{deployment.get('status', 'UNKNOWN')}[/]"]
    if (stage := deployment.get("lifecycleStage")) is not None:
        parts.append(f"stage [bold]{stage}[/]")
    if (task_counts := format_task_counts(deployment)) is not None:
        parts.append(task_counts)
    return " · ".join(parts)


def deployment_reached_terminal_state(deployment: ServiceDeploymentTypeDef, service: str) -> bool:
    """Return True only for a successful deployment — failed terminal states never return.

    Rollback and stop outcomes abort the whole command instead, so callers can treat
    a False return as "still in progress" without inspecting the status themselves.
    """
    if (status := deployment.get("status")) is None:
        console.error(f"Failed to get the status of the deployment for ECS service {service!r}")
        raise typer.Abort()

    if status == "SUCCESSFUL":
        console.success(f"ECS service {service!r} successfully deployed!")
        return True

    if status in {"ROLLBACK_SUCCESSFUL", "ROLLBACK_FAILED", "STOPPED"}:
        message = f"Deployment of ECS service {service!r} ended as {status}"
        if (reason := deployment.get("statusReason")) is not None:
            message = f"{message}: {reason}"
        console.error(message)
        raise typer.Abort()

    return False


def wait_for_deployments(ecs_client: ECSClient, deployments: dict[str, str]):
    """Poll every started deployment together, returning once all have succeeded.

    *deployments* maps each service name to its deployment ARN. Each round polls every service
    still in flight and logs its progress (prefixed with the service, on a transition or on the
    heartbeat so a long bake doesn't look like a hung job). A service reaching a failed terminal
    state aborts the whole command: the services are cross-gated on the same rollback alarms, so a
    fault in one rolls both back and there is nothing left to wait for.
    """
    console.info(f"Waiting for {len(deployments)} ECS deployment(s) to finish...")
    pending = dict(deployments)  # service -> arn, drained as each service succeeds
    last_progress: dict[str, str] = {}
    last_printed_at = 0.0
    while pending:
        now = time.monotonic()
        heartbeat = now - last_printed_at >= DEPLOYMENT_HEARTBEAT_SECONDS
        for service, deployment_arn in list(pending.items()):
            deployment = describe_service_deployment(ecs_client, deployment_arn)

            progress = f"{service}: {format_deployment_progress(deployment)}"
            if progress != last_progress.get(service) or heartbeat:
                console.info(progress)
                last_progress[service] = progress

            if deployment_reached_terminal_state(deployment, service):
                del pending[service]

        if heartbeat:
            last_printed_at = now
        if pending:
            time.sleep(DEPLOYMENT_POLL_INTERVAL_SECONDS)


def start_ecs_deployment(ecs_client: ECSClient, service: str, image: str) -> str:
    """Register a new task definition, roll the service onto it, and return the deployment ARN.

    Each ECS service runs in a cluster named after itself. This only starts the roll-out; the
    caller waits on the returned ARN so several services can bake concurrently.
    """
    task_definition_arn = register_task_definition(ecs_client, service, image)
    update_ecs_service(ecs_client, task_definition_arn, service=service, cluster=service)
    return find_service_deployment_arn(ecs_client, cluster=service, service=service)


def start_ecs_refresh(ecs_client: ECSClient, service: str) -> str:
    """Force a new deployment onto the service's latest ACTIVE task-definition revision.

    Passing the family name (which equals the service name) as the task definition lets ECS
    resolve it to the newest ACTIVE revision, adopting a terraform-registered task definition
    without registering a new one here. Returns the deployment ARN for the caller to wait on.
    """
    update_ecs_service(ecs_client, service, service=service, cluster=service, force_new_deployment=True)
    return find_service_deployment_arn(ecs_client, cluster=service, service=service)


def deploy(migrations_image: str, bot_image: str, alarm_action_image: str, events_image: str):
    lambda_client = boto3.client("lambda", region_name="eu-west-1")
    ecs_client = boto3.client("ecs", region_name="eu-west-1")

    update_lambda_code(lambda_client, "MitupMigrationsLambda", migrations_image)
    invoke_lambda(lambda_client, "MitupMigrationsLambda")
    update_lambda_code(lambda_client, "MitupAlarmActionLambda", alarm_action_image)

    # Start both roll-outs before waiting so their bake windows overlap: the rollback alarms are
    # cross-gated on both services, so a fault in either one can only roll both back while both bakes
    # are still open. The recurrent-events service takes the events image, never the bot image: the
    # slim bot image doesn't carry the `mitup recurrent-events` command.
    deployments = {
        "mitup": start_ecs_deployment(ecs_client, "mitup", bot_image),
        "mitup-recurrent-events": start_ecs_deployment(ecs_client, "mitup-recurrent-events", events_image),
    }
    wait_for_deployments(ecs_client, deployments)


def refresh():
    """Redeploy both ECS services onto their latest registered task definition — no lambdas, no migrations."""
    ecs_client = boto3.client("ecs", region_name="eu-west-1")

    # Start both refreshes before waiting so their bake windows overlap and the cross-gated rollback
    # alarms can react to a fault in either service while both bakes are still open.
    deployments = {
        "mitup": start_ecs_refresh(ecs_client, "mitup"),
        "mitup-recurrent-events": start_ecs_refresh(ecs_client, "mitup-recurrent-events"),
    }
    wait_for_deployments(ecs_client, deployments)
