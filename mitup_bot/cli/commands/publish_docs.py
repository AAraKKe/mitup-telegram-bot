import os
import subprocess
from time import sleep

import boto3
import click
from mypy_boto3_cloudfront import CloudFrontClient

from mitup_bot.cli.helpers import console, error, success


def s3_sync() -> list[str]:
    """
    Since boto3 does not have sync capabilities, we need to use the aws cli to sync files to s3.

    We want to use sync because the sync command only updloads those files that are different
    with respect to what is already in s3.

    We can use this list later to invalidate the CloudFormation cache only for these files.
    """
    bucket = f"s3://{os.environ["BOT_DOMAIN"]}/"

    console.rule(f"Syncing files to {bucket}")

    command = ["aws", "s3", "sync", "site", bucket, "--delete", "--size-only", "--no-progress"]
    outputs = subprocess.Popen(command, stdout=subprocess.PIPE).communicate()[0]

    lines = list(outputs.decode().split("\n"))

    uploaded = []
    for line in lines:
        if not line:
            # Avoid empty lines that can come with the logs
            continue
        console.print(line)
        uploaded.append(line.replace("site/", "/").split()[1])

    return uploaded


def get_distribution_id(client: CloudFrontClient) -> str:
    """
    Get the distribution ID for the CloudFront distribution that serves the site.
    """
    distributions = client.list_distributions()

    if distributions["ResponseMetadata"]["HTTPStatusCode"] != 200:
        error(f"Failed to get the distribution ID. Response: {distributions}")
        raise click.Abort()

    distribution = distributions["DistributionList"].get("Items")

    if distribution is None:
        error(f"No distributions found. Reponse: {distribution}")
        console.print(distributions)
        raise click.Abort()

    console.print("[bold]Distribution ID[/bold]:", distribution[0]["Id"])

    return distribution[0]["Id"]


@click.command()
def cli():
    """
    This command invalidates the CloudFormation cache after files have been updated to s3.

    The reason why we do not want to just invalidate the whole cache is because of costs. By having
    a command that invlidates only those files that have been updated, minimizing the number o paths
    requested, we can reduce costs while still havinga a 24h TTL on the cache.
    """
    docs_files_updated = s3_sync()

    if docs_files_updated == []:
        console.print("No files have been updated. No need to invalidate the cache.")
        return

    console.rule("Invalidating CloudFront cache")

    # Cloufront sits in us-east-1 as it uses Edge to reach to other regions
    client = boto3.client("cloudfront", region_name="us-east-1")
    distribution_id = get_distribution_id(client)

    request = {
        "DistributionId": distribution_id,
        "InvalidationBatch": {
            "Paths": {
                "Quantity": len(docs_files_updated),
                "Items": docs_files_updated,
            },
            "CallerReference": f"mitup-ci-{os.environ['CI_COMMIT_SHORT_SHA']}",
        },
    }
    console.print("[bold]Invalidation request[/bold]:")
    console.print(request)
    response = client.create_invalidation(**request)

    if response["ResponseMetadata"]["HTTPStatusCode"] != 201:
        error(f"Failed to invalidate the CloudFront cache. Response: {response}")
        raise click.Abort()

    response = client.get_invalidation(DistributionId=distribution_id, Id=response["Invalidation"]["Id"])
    while response["Invalidation"]["Status"] != "Completed":
        response = client.get_invalidation(DistributionId=distribution_id, Id=response["Invalidation"]["Id"])
        sleep(10)

    success("CloudFront cache has been invalidated")
