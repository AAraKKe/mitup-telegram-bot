import base64
import json
import time

import boto3
import click
from mypy_boto3_ecs import ECSClient
from mypy_boto3_ecs.type_defs import DeploymentTypeDef, DescribeServicesResponseTypeDef
from mypy_boto3_lambda import LambdaClient
from rich.console import Console
from rich.status import Status

console = Console(width=90)


def error(msg: str):
    console.print(f"[bold red]✘ {msg}[/]")


def success(msg: str):
    console.print(f"[bold green]✔︎ {msg}[/]")


def update_lambda_code(lambda_client: LambdaClient, name: str, image: str):
    with console.status(f"Updating {name} lambda code..."):
        lambda_client.update_function_code(FunctionName=name, ImageUri=image, Publish=True)
        function = lambda_client.get_function(FunctionName=name)
        status = function["Configuration"]["LastUpdateStatus"]
        while status == "InProgress":
            function = lambda_client.get_function(FunctionName=name)
            status = function["Configuration"]["LastUpdateStatus"]
            # Lets call every 5 seconds
            time.sleep(5)

        if status == "Failed":
            error(f"Failed updating code for lambda {name} for the following reason:")
            console.print(function["Configuration"]["LastUpdateStatusReason"])
            raise click.Abort()

        success(f"Lambda {name} function code updated")


def invoke_lambda(lambda_client: LambdaClient, name: str):
    with console.status(f"Invoking lambda function {name}..."):
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
                console.print(line)
            else:
                if "log_level" in json_event and json_event["log_level"] == "ERROR":
                    # Error log
                    console.print(f"[bold red]Lambda failed execution: {json_event['errorMessage']}[/]")
                    console.print("Stack trace:")
                    for stack_line in json_event["stackTrace"]:
                        console.print(stack_line)
                if "type" in json_event and json_event["type"] == "platform.report":
                    exec_time = json_event["record"]["metrics"]["durationMs"]
                    # Report execution, can get ifnormation about time
                    console.rule(f"[bold]Lambda {name} run in {exec_time} ms")

        if response["StatusCode"] != 200 or "FunctionError" in response:
            function_error = response["FunctionError"]
            error(f"Error invoking lambda {name}: {function_error}")
            raise click.Abort()

        success(f"Lambda {name} finished successfully")


def register_task_definition(ecs_client: ECSClient, family: str, image: str) -> str:
    with console.status(f"Registering task definition {family!r}..."):
        console.print(f"ECR image: {image}")

        task_def = ecs_client.describe_task_definition(taskDefinition=family)
        containers_definition = task_def["taskDefinition"]["containerDefinitions"][0]
        containers_definition["image"] = image
        execution_role = task_def["taskDefinition"]["executionRoleArn"]

        response = ecs_client.register_task_definition(
            family=family,
            containerDefinitions=[containers_definition],
            executionRoleArn=execution_role,
        )
        status_code = response["ResponseMetadata"]["HTTPStatusCode"]

        if status_code != 200:
            error(f"Error registering task definition for {family!r}: [StatusCode: {status_code}]")
            raise click.Abort()

        revision = response["taskDefinition"]["revision"]
        success(f"New task definition defined for family {family!r}, revision: {revision}")
        return response["taskDefinition"]["taskDefinitionArn"]


def update_ecs_service(ecs_client: ECSClient, task_definition: str, service: str, cluster: str):
    with console.status(f"Updating ECS service {service!r}"):
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


def update_status_for_deployment(
    status: Status,
    response: DescribeServicesResponseTypeDef,
    task_definition_arn: str,
) -> DeploymentTypeDef:
    service = response["services"][0]

    # Get the number of tasks in different stages
    # We are using get with these properties because they are not ensured to exist
    desired_count = service.get("desiredCount")
    running_count = service.get("runningCount")
    pending_count = service.get("pendingCount")

    deployments = service.get("deployments", [])

    # Get the deployment state
    task_deployments = [
        deployment for deployment in deployments if deployment.get("taskDefinition", "") == task_definition_arn
    ]

    if not task_deployments:
        error(
            f"No deployment can be found for the task deifnition requested {task_definition_arn!r}. "
            "Check wheather or not the deployments are triggered and further improve this deployment if needed."
        )
        raise click.Abort()

    if len(task_deployments) > 1:
        error(
            f"More than one deployments have been found for the task deifnition requested {task_definition_arn!r}. "
            "Check wheather or not the deployments are triggered and further improve this deployment if needed."
        )
        raise click.Abort()

    deployment = task_deployments[0]
    state = deployment.get("rolloutState")

    desired_str = f"Desired: [bold]{desired_count}[/]"
    running_str = f"Running: [bold]{running_count}[/]"
    pending_str = f"Pending: [bold]{pending_count}[/]"
    status.update(f"Deployment: {state} | Tasks: [ {desired_str}, {running_str}, {pending_str} ]")
    return deployment


def waiting_for_deployment_to_finish(ecs_client: ECSClient, cluster: str, service: str, task_definition_arn: str):
    with console.status(f"Waiting for ECS service {service!r} to deploy") as status:
        response = ecs_client.describe_services(services=[service], cluster=cluster)
        deployment = update_status_for_deployment(status, response, task_definition_arn=task_definition_arn)

        while deployment["rolloutState"] == "IN_PROGRESS":
            # This can take a bit of time, lets loop and give some information
            time.sleep(5)
            response = ecs_client.describe_services(services=[service], cluster=cluster)
            deployment = update_status_for_deployment(status, response, task_definition_arn=task_definition_arn)

        if deployment["rolloutState"] == "FAILED":
            reason = deployment["rolloutStateReason"]
            error(f"Failed latest deployment: {reason}")
            raise click.Abort()

        success(f"ECS service {service!r} successfuly deployed!")


@click.command()
@click.option("--migrations-image", help="Uri of the lambda image pushed to ECR")
@click.option("--bot-image", help="Uri of the bot image pushed to ECR")
def cli(migrations_image: str, bot_image: str):
    lambda_client = boto3.client("lambda", region_name="eu-west-1")
    ecs_client = boto3.client("ecs", region_name="eu-west-1")

    update_lambda_code(lambda_client, "MitupMigrationsLambda", migrations_image)
    invoke_lambda(lambda_client, "MitupMigrationsLambda")
    task_definition_arn = register_task_definition(ecs_client, "mitup", bot_image)
    update_ecs_service(ecs_client, "mitup", "mitup", "mitup")
    waiting_for_deployment_to_finish(ecs_client, "mitup", "mitup", task_definition_arn)
