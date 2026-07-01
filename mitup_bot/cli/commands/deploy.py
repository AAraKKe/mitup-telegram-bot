import base64
import json
import time

import boto3
import click
from mypy_boto3_ecs import ECSClient
from mypy_boto3_ecs.type_defs import ServiceDeploymentTypeDef
from mypy_boto3_lambda import LambdaClient
from mypy_boto3_lambda.type_defs import FunctionConfigurationTypeDef

from mitup_bot.cli.helpers import console, error, success

LAMBDA_POLL_INTERVAL_SECONDS = 5
DEPLOYMENT_POLL_INTERVAL_SECONDS = 10
DEPLOYMENT_HEARTBEAT_SECONDS = 60
DEPLOYMENT_DISCOVERY_ATTEMPTS = 6
DEPLOYMENT_DISCOVERY_DELAY_SECONDS = 5


def wait_for_lambda_update(lambda_client: LambdaClient, name: str) -> FunctionConfigurationTypeDef:
    while True:
        configuration = lambda_client.get_function(FunctionName=name)["Configuration"]
        if (status := configuration.get("LastUpdateStatus")) is None:
            error(f"Failed to get the status of the lambda function {name!r}")
            raise click.Abort()

        if status != "InProgress":
            return configuration

        time.sleep(LAMBDA_POLL_INTERVAL_SECONDS)


def update_lambda_code(lambda_client: LambdaClient, name: str, image: str):
    console().print(f"Updating {name} lambda code...")
    lambda_client.update_function_code(FunctionName=name, ImageUri=image, Publish=True)

    configuration = wait_for_lambda_update(lambda_client, name)
    if configuration.get("LastUpdateStatus") == "Failed":
        error(f"Failed updating code for lambda {name} for the following reason:")
        console().print(configuration.get("LastUpdateStatusReason"))
        raise click.Abort()

    success(f"Lambda {name} function code updated")


def invoke_lambda(lambda_client: LambdaClient, name: str):
    console().print(f"Invoking lambda function {name}...")
    payload = {"action": "upgrade", "revision": "head"}
    response = lambda_client.invoke(
        FunctionName=name,
        LogType="Tail",
        Payload=json.dumps(payload),
    )

    console().rule("[bold]Log[/bold]")
    result = base64.b64decode(response["LogResult"]).decode().splitlines()
    for line in result:
        if not line:
            # Avoid empty lines that can come with the logs
            continue

        try:
            json_event = json.loads(line)
        except json.JSONDecodeError:
            # Cannot parse as json, i.e. it is just a line
            console().print(line)
        else:
            if "log_level" in json_event and json_event["log_level"] == "ERROR":
                # Error log
                console().print(f"[bold red]Lambda failed execution: {json_event['errorMessage']}[/]")
                console().print("Stack trace:")
                for stack_line in json_event["stackTrace"]:
                    console().print(stack_line)
            if "type" in json_event and json_event["type"] == "platform.report":
                exec_time = json_event["record"]["metrics"]["durationMs"]
                # Report execution, can get ifnormation about time
                console().rule(f"[bold]Lambda {name} run in {exec_time} ms")

    if response["StatusCode"] != 200 or "FunctionError" in response:
        function_error = response["FunctionError"]
        error(f"Error invoking lambda {name}: {function_error}")
        raise click.Abort()

    success(f"Lambda {name} finished successfully")


def register_task_definition(ecs_client: ECSClient, family: str, image: str) -> str:
    console().print(f"Registering task definition {family!r}...")
    console().print(f"ECR image: {image}")

    task_def = ecs_client.describe_task_definition(taskDefinition=family)
    if (containers_definition := task_def["taskDefinition"].get("containerDefinitions")) is None:
        error(f"Task definition {family!r} does not have any container definitions")
        raise click.Abort()

    containers_definition[0]["image"] = image
    if (execution_role := task_def["taskDefinition"].get("executionRoleArn")) is None:
        error(f"Task definition {family!r} does not have an execution role")
        raise click.Abort()

    response = ecs_client.register_task_definition(
        family=family,
        containerDefinitions=containers_definition,
        executionRoleArn=execution_role,
    )
    if (status_code := response["ResponseMetadata"].get("HTTPStatusCode")) is None:
        error(f"Failed to get status code for registering task definition {family!r}")
        raise click.Abort()

    if status_code != 200:
        error(f"Error registering task definition for {family!r}: [StatusCode: {status_code}]")
        raise click.Abort()

    if (revision := response["taskDefinition"].get("revision")) is None:
        error(f"Failed to get revision for task definition {family!r}")
        raise click.Abort()

    success(f"New task definition defined for family {family!r}, revision: {revision}")

    if (arn := response["taskDefinition"].get("taskDefinitionArn")) is None:
        error(f"Failed to get task definition ARN for {family!r}")
        raise click.Abort()

    return arn


def update_ecs_service(ecs_client: ECSClient, task_definition: str, service: str, cluster: str):
    console().print(f"Updating ECS service {service!r}...")
    response = ecs_client.update_service(
        cluster=cluster,
        service=service,
        taskDefinition=task_definition,
    )

    status_code = response["ResponseMetadata"]["HTTPStatusCode"]
    if status_code != 200:
        error(f"Error updating ECS service {service!r}. StatusCode: {status_code}")
        raise click.Abort()
    success(f"ECS service {service!r} has been updated")


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

    error(f"No active deployment found for ECS service {service!r} after updating it")
    raise click.Abort()


def describe_service_deployment(ecs_client: ECSClient, deployment_arn: str) -> ServiceDeploymentTypeDef:
    response = ecs_client.describe_service_deployments(serviceDeploymentArns=[deployment_arn])
    deployments = response["serviceDeployments"]
    if not deployments:
        error(f"Failed to describe the service deployment {deployment_arn!r}")
        raise click.Abort()

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
        error(f"Failed to get the status of the deployment for ECS service {service!r}")
        raise click.Abort()

    if status == "SUCCESSFUL":
        success(f"ECS service {service!r} successfully deployed!")
        return True

    if status in {"ROLLBACK_SUCCESSFUL", "ROLLBACK_FAILED", "STOPPED"}:
        message = f"Deployment of ECS service {service!r} ended as {status}"
        if (reason := deployment.get("statusReason")) is not None:
            message = f"{message}: {reason}"
        error(message)
        raise click.Abort()

    return False


def waiting_for_deployment_to_finish(ecs_client: ECSClient, cluster: str, service: str):
    console().print(f"Waiting for ECS service {service!r} to deploy...")
    deployment_arn = find_service_deployment_arn(ecs_client, cluster=cluster, service=service)

    last_progress: str | None = None
    last_printed_at = 0.0
    while True:
        deployment = describe_service_deployment(ecs_client, deployment_arn)

        # Only log transitions, plus a heartbeat so a long bake doesn't look like a hung job
        progress = format_deployment_progress(deployment)
        now = time.monotonic()
        if progress != last_progress or now - last_printed_at >= DEPLOYMENT_HEARTBEAT_SECONDS:
            console().print(progress)
            last_progress = progress
            last_printed_at = now

        if deployment_reached_terminal_state(deployment, service):
            return

        time.sleep(DEPLOYMENT_POLL_INTERVAL_SECONDS)


@click.command()
@click.option("--migrations-image", help="Uri of the migrations lambda image pushed to ECR")
@click.option("--bot-image", help="Uri of the bot image pushed to ECR")
@click.option("--alarm-action-image", help="Uri of the alarm action lambda image pushed to ECR")
def cli(migrations_image: str, bot_image: str, alarm_action_image: str):
    lambda_client = boto3.client("lambda", region_name="eu-west-1")
    ecs_client = boto3.client("ecs", region_name="eu-west-1")

    update_lambda_code(lambda_client, "MitupMigrationsLambda", migrations_image)
    invoke_lambda(lambda_client, "MitupMigrationsLambda")
    update_lambda_code(lambda_client, "MitupAlarmActionLambda", alarm_action_image)

    # Both services run in a cluster named after themselves, off the same bot image
    for service in ("mitup", "mitup-recurrent-events"):
        task_definition_arn = register_task_definition(ecs_client, service, bot_image)
        update_ecs_service(ecs_client, task_definition_arn, service=service, cluster=service)
        waiting_for_deployment_to_finish(ecs_client, cluster=service, service=service)
