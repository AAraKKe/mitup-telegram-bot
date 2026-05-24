"""Lambda entry point for the Rails → Python data migration.

The function looks up the Rails DSN from Secrets Manager (so the value never lands in
plain Lambda env vars) and dispatches the CLI command's programmatic entry point.

Event schema::

    {
        "dry_run": true | false,  # optional, default true
        "phases": "users,meetups,...",  # optional, defaults to every phase
        "rails_secret_arn": "arn:...",  # optional, defaults to RAILS_DB_SECRET_ARN env var
        "archive_s3_uri": "s3://...",  # optional, defaults to MIGRATE_ARCHIVE_S3_URI env var
    }
"""

import json
import os
from typing import Any

import boto3

from mitup_bot.cli.commands.migrate_from_rails import ALL_PHASES, invoke_from_lambda


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    # dry_run defaults to True here for Lambda safety; the CLI defaults to False.
    dry_run = bool(event.get("dry_run", True))
    phases = event.get("phases") or ",".join(ALL_PHASES)
    secret_arn = event.get("rails_secret_arn") or os.environ["RAILS_DB_SECRET_ARN"]
    archive_uri = event.get("archive_s3_uri") or os.environ.get("MIGRATE_ARCHIVE_S3_URI")

    rails_url = fetch_rails_dsn(secret_arn)

    invoke_from_lambda(rails_url=rails_url, archive_s3_uri=archive_uri, dry_run=dry_run, phases=phases)
    return {"status": "ok", "dry_run": dry_run, "phases": phases}


def fetch_rails_dsn(secret_arn: str) -> str:
    client = boto3.client("secretsmanager", region_name="eu-west-1")
    response = client.get_secret_value(SecretId=secret_arn)
    secret = response.get("SecretString")
    if secret is None:
        raise RuntimeError(f"Secret {secret_arn!r} has no SecretString payload")
    # Support either a raw DSN string or a JSON blob with a "dsn" key.
    try:
        parsed = json.loads(secret)
    except json.JSONDecodeError:
        return secret
    if isinstance(parsed, dict) and "dsn" in parsed:
        return str(parsed["dsn"])
    return secret
