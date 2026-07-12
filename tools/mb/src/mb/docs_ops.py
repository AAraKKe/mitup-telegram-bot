import os
import subprocess
from pathlib import Path
from time import sleep

import boto3
import typer
from mypy_boto3_cloudfront import CloudFrontClient
from mypy_boto3_cloudfront.type_defs import (
    CreateInvalidationRequestTypeDef,
    InvalidationBatchTypeDef,
    PathsTypeDef,
)

from . import console

# HTML pages must revalidate on every request so docs edits show up immediately
# once CloudFront has been invalidated. Other assets get a one-hour browser
# cache (with revalidation after) so unhashed custom CSS rolls out promptly
# without forcing a revalidation round-trip on every page load for hashed
# theme bundles.
HTML_CACHE_CONTROL = "public, max-age=0, must-revalidate"
ASSET_CACHE_CONTROL = "public, max-age=3600, must-revalidate"


def stream_command(command: list[str]):
    """Run a command, stream its output line-by-line, raise on non-zero exit."""
    with subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ) as process:
        if process.stdout:
            for line in process.stdout:
                console.info(line.rstrip())
        returncode = process.wait()

    if returncode != 0:
        raise RuntimeError(f"Command failed ({' '.join(command[:3])}…): exited with status {returncode}")


def s3_sync():
    """Push `site/` to S3 as two scoped `sync` passes (HTML and non-HTML) so each upload gets the right cache-control AND the right Content-Type."""  # noqa: E501
    bucket = f"s3://{os.environ['BOT_DOMAIN']}/"

    # Refresh local mtimes so `aws s3 sync` always uploads.
    for path in Path("site").rglob("*"):
        if path.is_file():
            path.touch()

    # We deliberately use two `sync` passes instead of one `sync` + `cp REPLACE`:
    # `cp --metadata-directive REPLACE` wipes Content-Type back to
    # `binary/octet-stream` unless every header is restated explicitly, which
    # would make browsers refuse to apply CSS/JS. `sync` instead infers
    # Content-Type from the file extension at upload time.
    console.rule(f"Syncing HTML to {bucket}")
    stream_command(
        [
            "aws",
            "s3",
            "sync",
            "site",
            bucket,
            "--exclude",
            "*",
            "--include",
            "*.html",
            "--cache-control",
            HTML_CACHE_CONTROL,
            "--delete",
            "--no-progress",
        ]
    )

    console.rule(f"Syncing assets to {bucket}")
    stream_command(
        [
            "aws",
            "s3",
            "sync",
            "site",
            bucket,
            "--exclude",
            "*.html",
            "--cache-control",
            ASSET_CACHE_CONTROL,
            "--delete",
            "--no-progress",
        ]
    )


def get_distribution_id(client: CloudFrontClient) -> str:
    """Return the ID of the CloudFront distribution whose aliases include `BOT_DOMAIN`.

    The account may host several distributions, so we match on the alias rather
    than taking the first one. A CloudFront alias is unique across the account,
    so at most one distribution can match.
    """
    distributions = client.list_distributions()

    if distributions["ResponseMetadata"]["HTTPStatusCode"] != 200:
        console.error(f"Failed to get the distribution ID. Response: {distributions}")
        raise typer.Abort()

    items = distributions["DistributionList"].get("Items")

    if not items:
        console.error(f"No distributions found. Response: {distributions}")
        console.show(distributions)
        raise typer.Abort()

    bot_domain = os.environ["BOT_DOMAIN"]
    for item in items:
        if bot_domain in (item["Aliases"].get("Items") or []):
            console.info(f"[bold]Distribution ID[/bold]: {item['Id']}")
            return item["Id"]

    console.error(f"No CloudFront distribution has {bot_domain} as an alias. Response: {distributions}")
    console.show(distributions)
    raise typer.Abort()


def publish_docs():
    """Sync `site/` to S3 and invalidate `/*` on CloudFront (one path against the 1000-per-month free tier)."""
    s3_sync()

    console.rule("Invalidating CloudFront cache")

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
    console.info("[bold]Invalidation request[/bold]:")
    console.show(request)
    response = client.create_invalidation(**request)

    if response["ResponseMetadata"]["HTTPStatusCode"] != 201:
        console.error(f"Failed to invalidate the CloudFront cache. Response: {response}")
        raise typer.Abort()

    invalidation_id = response["Invalidation"]["Id"]
    response = client.get_invalidation(DistributionId=distribution_id, Id=invalidation_id)
    while response["Invalidation"]["Status"] != "Completed":
        sleep(10)
        response = client.get_invalidation(DistributionId=distribution_id, Id=invalidation_id)

    console.success("CloudFront cache has been invalidated")
