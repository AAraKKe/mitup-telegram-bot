import os
import subprocess
from pathlib import Path
from time import sleep

import boto3
import click
from mypy_boto3_cloudfront import CloudFrontClient
from mypy_boto3_cloudfront.type_defs import (
    CreateInvalidationRequestTypeDef,
    InvalidationBatchTypeDef,
    PathsTypeDef,
)

from mitup_bot.cli.helpers import console, error, success

# HTML pages must revalidate on every request so docs edits show up immediately
# once CloudFront has been invalidated. Other assets get a one-hour browser
# cache (with revalidation after) so unhashed custom CSS rolls out promptly
# without forcing a revalidation round-trip on every page load for hashed
# theme bundles.
HTML_CACHE_CONTROL = "public, max-age=0, must-revalidate"
ASSET_CACHE_CONTROL = "public, max-age=3600, must-revalidate"


def run_command(command: list[str]):
    """Run a command, stream its output line-by-line, raise on non-zero exit."""
    with subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ) as process:
        if process.stdout:
            for line in process.stdout:
                console().print(line.rstrip())
        returncode = process.wait()

    if returncode != 0:
        raise RuntimeError(f"Command failed ({' '.join(command[:3])}…): exited with status {returncode}")


def s3_sync():
    """Push `site/` to S3 in two passes: HTML cache-control + `--delete`, then `cp REPLACE` to stamp asset cache-control on non-HTML objects."""  # noqa: E501
    bucket = f"s3://{os.environ['BOT_DOMAIN']}/"

    # Refresh local mtimes so `aws s3 sync` always uploads.
    for path in Path("site").rglob("*"):
        if path.is_file():
            path.touch()

    console().rule(f"Syncing files to {bucket}")
    run_command(
        [
            "aws",
            "s3",
            "sync",
            "site",
            bucket,
            "--cache-control",
            HTML_CACHE_CONTROL,
            "--delete",
            "--no-progress",
        ]
    )

    console().rule("Stamping non-HTML objects with asset cache-control")
    run_command(
        [
            "aws",
            "s3",
            "cp",
            bucket,
            bucket,
            "--recursive",
            "--exclude",
            "*.html",
            "--cache-control",
            ASSET_CACHE_CONTROL,
            "--metadata-directive",
            "REPLACE",
            "--no-progress",
        ]
    )


def get_distribution_id(client: CloudFrontClient) -> str:
    """Get the distribution ID for the CloudFront distribution that serves the site."""
    distributions = client.list_distributions()

    if distributions["ResponseMetadata"]["HTTPStatusCode"] != 200:
        error(f"Failed to get the distribution ID. Response: {distributions}")
        raise click.Abort()

    distribution = distributions["DistributionList"].get("Items")

    if distribution is None:
        error(f"No distributions found. Response: {distributions}")
        console().print(distributions)
        raise click.Abort()
    elif not distribution:
        error(f"No distributions found (empty list). Response: {distributions}")
        console().print(distributions)
        raise click.Abort()

    console().print("[bold]Distribution ID[/bold]:", distribution[0]["Id"])

    return distribution[0]["Id"]


@click.command()
def cli():
    """Sync `site/` to S3 and invalidate `/*` on CloudFront (one path against the 1000-per-month free tier)."""
    s3_sync()

    console().rule("Invalidating CloudFront cache")

    # CloudFront sits in us-east-1 as it uses Edge to reach to other regions
    client = boto3.client("cloudfront", region_name="us-east-1")
    distribution_id = get_distribution_id(client)

    paths: PathsTypeDef = {"Quantity": 1, "Items": ["/*"]}
    batch: InvalidationBatchTypeDef = {
        "Paths": paths,
        "CallerReference": f"mitup-ci-{os.environ['CI_COMMIT_SHORT_SHA']}",
    }
    request: CreateInvalidationRequestTypeDef = {
        "DistributionId": distribution_id,
        "InvalidationBatch": batch,
    }
    console().print("[bold]Invalidation request[/bold]:")
    console().print(request)
    response = client.create_invalidation(**request)

    if response["ResponseMetadata"]["HTTPStatusCode"] != 201:
        error(f"Failed to invalidate the CloudFront cache. Response: {response}")
        raise click.Abort()

    invalidation_id = response["Invalidation"]["Id"]
    response = client.get_invalidation(DistributionId=distribution_id, Id=invalidation_id)
    while response["Invalidation"]["Status"] != "Completed":
        sleep(10)
        response = client.get_invalidation(DistributionId=distribution_id, Id=invalidation_id)

    success("CloudFront cache has been invalidated")
