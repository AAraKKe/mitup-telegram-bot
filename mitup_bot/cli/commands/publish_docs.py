import os
import subprocess
from time import sleep
from urllib.parse import urlparse

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
    bucket = f"s3://{os.environ['BOT_DOMAIN']}/"

    console().rule(f"Syncing files to {bucket}")

    command = ["aws", "s3", "sync", "site", bucket, "--delete", "--size-only", "--no-progress"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()

    # Check if the command failed
    if process.returncode != 0:
        raise RuntimeError(f"Failed to sync files to S3. Error: {stderr.decode().strip()}")

    lines = list(stdout.decode().split("\n"))

    uploaded = []
    for line in lines:
        if not line:
            # Avoid empty lines that can come with the logs
            continue
        console().print(line)

        parts = line.split()
        s3_uri = ""
        if parts[0] == "upload:":
            # e.g., "upload: site/foo.html to s3://bucket/foo.html" -> parts[3] is the s3 uri
            s3_uri = parts[3]
        elif parts[0] == "delete:":
            # e.g., "delete: s3://bucket/bar.html" -> parts[1] is the s3 uri
            s3_uri = parts[1]

        if s3_uri:
            # Parse the S3 URI and get the path component
            parsed_uri = urlparse(s3_uri)
            path = parsed_uri.path
            if path:  # Only append if a path was actually found
                uploaded.append(path)
            else:
                console().print(f"[yellow]Could not parse path from S3 URI:[/yellow] {s3_uri}")
        else:
            # Log unexpected lines if necessary, or just skip
            console().print(f"[yellow]Skipping unexpected sync log line:[/yellow] {line}")

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
        error(f"No distributions found. Response: {distributions}")
        console().print(distributions)
        raise click.Abort()
    elif not distribution:  # Check if the list is empty
        error(f"No distributions found (empty list). Response: {distributions}")
        console().print(distributions)
        raise click.Abort()

    console().print("[bold]Distribution ID[/bold]:", distribution[0]["Id"])

    return distribution[0]["Id"]


@click.command()
@click.option(
    "--invalidate-all",
    is_flag=True,
    default=False,
    help="Invalidate the entire CloudFront cache (/*) instead of only updated files.",
)
def cli(invalidate_all: bool):
    """
    This command synchronizes the 'site' directory to S3 and invalidates the CloudFront cache.

    The reason why we do not want to just invalidate the whole cache is because of costs. By having
    a command that invlidates only those files that have been updated, minimizing the number o paths
    requested, we can reduce costs while still havinga a 24h TTL on the cache.
    """
    docs_files_updated = s3_sync()

    # Only exit early if we are NOT doing invalidate_all AND no files were updated
    if not invalidate_all and not docs_files_updated:
        console().print("No files have been updated. No need to invalidate the cache.")
        return

    console().rule("Invalidating CloudFront cache")

    # Cloufront sits in us-east-1 as it uses Edge to reach to other regions
    client = boto3.client("cloudfront", region_name="us-east-1")
    distribution_id = get_distribution_id(client)

    # Determine paths to invalidate
    if invalidate_all:
        console().print("[bold yellow]Invalidating entire cache (/*)[/bold yellow]")
        paths_to_invalidate = ["/*"]
    else:
        paths_to_invalidate = docs_files_updated

    # Build the invalidation request
    request = {
        "DistributionId": distribution_id,
        "InvalidationBatch": {
            "Paths": {
                "Quantity": len(paths_to_invalidate),
                "Items": paths_to_invalidate,
            },
            "CallerReference": f"mitup-ci-{os.environ['CI_COMMIT_SHORT_SHA']}",
        },
    }
    console().print("[bold]Invalidation request[/bold]:")
    console().print(request)
    response = client.create_invalidation(**request)

    if response["ResponseMetadata"]["HTTPStatusCode"] != 201:
        error(f"Failed to invalidate the CloudFront cache. Response: {response}")
        raise click.Abort()

    response = client.get_invalidation(DistributionId=distribution_id, Id=response["Invalidation"]["Id"])
    while response["Invalidation"]["Status"] != "Completed":
        response = client.get_invalidation(DistributionId=distribution_id, Id=response["Invalidation"]["Id"])
        sleep(10)

    success("CloudFront cache has been invalidated")
