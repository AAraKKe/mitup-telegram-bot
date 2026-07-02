"""Lambda entry point for CloudWatch alarm actions (Dec-2023 alarm → Lambda feature).

CloudWatch invokes this function asynchronously when any alarm transitions to a new state.
It forwards a generic alert to GitLab's HTTP Endpoint alert integration so that GitLab's
own downstream notification pipeline handles the Telegram/email fanout. When the alarm
recovers (state == "OK"), the payload includes an ``end_time`` field that signals GitLab to
auto-resolve the open alert with the matching fingerprint.

This lambda is intentionally generic — it never references a specific alarm or metric.
All payload fields are derived from the incoming event, so the same function can serve
every future alarm without modification.

Environment variables
---------------------
GITLAB_ALERT_SSM_PARAM : str
    Name of an SSM SecureString parameter whose decrypted value is a JSON object:
    {"webhook_url": "<GitLab HTTP Endpoint webhook URL>", "authorization_key": "<authorization key>"}

LOG_LEVEL : str (optional, default "INFO")
    Structlog/stdlib log level passed to configure_logging.
"""

import json
import os
from typing import Any
from urllib.parse import quote

import boto3
import httpx
import structlog
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from mitup_bot.config import Env
from mitup_bot.logging_config import configure_logging

log = structlog.get_logger(__name__)

# Timeout applied to the GitLab HTTP endpoint POST request.
_GITLAB_POST_TIMEOUT_S = 10


# --- Pydantic models for the CloudWatch alarm event ---


class AlarmState(BaseModel):
    # CloudWatch sends camelCase keys (e.g. "reasonData"); alias_generator maps them to snake_case.
    model_config = ConfigDict(extra="allow", alias_generator=to_camel, populate_by_name=True)

    value: str
    reason: str | None = None
    reason_data: str | None = None  # JSON string — kept as-is, never parsed
    timestamp: str | None = None


class AlarmPreviousState(BaseModel):
    model_config = ConfigDict(extra="allow", alias_generator=to_camel, populate_by_name=True)

    value: str | None = None
    reason: str | None = None
    reason_data: str | None = None  # JSON string — kept as-is on the model, parsed when building payload
    timestamp: str | None = None


class AlarmData(BaseModel):
    model_config = ConfigDict(extra="allow", alias_generator=to_camel, populate_by_name=True)

    alarm_name: str
    state: AlarmState
    previous_state: AlarmPreviousState | None = None
    configuration: dict[str, Any] | None = None


class AlarmEvent(BaseModel):
    """Minimal validated shape for a CloudWatch metric-alarm or composite-alarm event.

    Only the fields required to build a meaningful GitLab alert are required; everything
    else is optional so that composite-alarm and log-alarm variants pass through without
    schema errors.
    """

    model_config = ConfigDict(extra="allow", alias_generator=to_camel, populate_by_name=True)

    source: str
    alarm_arn: str
    account_id: str
    region: str
    time: str
    alarm_data: AlarmData


# --- SSM credential fetch (isolated so tests can mock it cleanly) ---


class GitLabAlertCredentials(BaseModel):
    # GitLab's HTTP Endpoint alert integration provides a "webhook URL" and an "authorization key".
    webhook_url: str
    authorization_key: str


def fetch_gitlab_credentials(param_name: str) -> GitLabAlertCredentials:
    """Fetch and decode the GitLab HTTP endpoint credentials from SSM Parameter Store."""
    ssm = boto3.client("ssm")
    response = ssm.get_parameter(Name=param_name, WithDecryption=True)
    raw = response["Parameter"]["Value"]
    return GitLabAlertCredentials.model_validate(json.loads(raw))


# --- GitLab alert payload builder ---


def maybe_parse_json(value: str | None) -> Any:
    """Return the JSON-decoded object when value is valid JSON, otherwise return value unchanged.

    reasonData arrives as a JSON string on the wire but is not guaranteed to be valid JSON
    (CloudWatch may emit plain text). Never raises — callers can always rely on getting
    something back that is safe to include in a JSON payload.
    """
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError, ValueError:
        return value


def extract_metric_namespace(alarm_data: AlarmData) -> str | None:
    """Return the first metric namespace found in the alarm configuration, or None.

    Navigates defensively because configuration may be absent (composite/log alarms),
    metrics entries may be metric-math expressions with no metricStat, and the structure
    itself may differ across alarm types.
    """
    if alarm_data.configuration is None:
        return None
    metrics = alarm_data.configuration.get("metrics")
    if not metrics:
        return None
    for metric_entry in metrics:
        try:
            namespace = metric_entry["metricStat"]["metric"]["namespace"]
        except KeyError, TypeError:
            continue
        if namespace:
            return str(namespace)
    return None


def build_alarm_console_url(region: str, alarm_name: str) -> str:
    """Deep link to the alarm's detail page in the CloudWatch console."""
    return (
        f"https://{region}.console.aws.amazon.com/cloudwatch/home"
        f"?region={region}#alarmsV2:alarm/{quote(alarm_name, safe='')}"
    )


def build_gitlab_payload(alarm: AlarmEvent) -> dict[str, Any]:
    """Build a generic GitLab alert payload from the alarm event.

    Severity is set to "critical" for ALARM state and "info" for all others.
    This is a sane generic default; it is NOT alarm-specific business logic.
    Custom alarms that need finer-grained severity should use separate lambdas
    or additional metadata in the alarm description.

    When the alarm has recovered (state == "OK"), ``end_time`` is included so
    that GitLab auto-resolves the open alert with the matching fingerprint.
    """
    state = alarm.alarm_data.state
    previous_state_value = alarm.alarm_data.previous_state.value if alarm.alarm_data.previous_state else None

    severity = "critical" if state.value == "ALARM" else "info"
    # The console URL goes in two places: alarm_url (clickable in GitLab's alert-details view)
    # and the description — the only field that reliably survives into GitLab's downstream
    # Telegram/email notification fanout.
    alarm_url = build_alarm_console_url(alarm.region, alarm.alarm_data.alarm_name)
    reason = state.reason or "CloudWatch alarm state changed"
    description = f"{reason}\n\nAlarm: {alarm_url}"

    # Setting end_time on a payload whose fingerprint matches an open GitLab alert
    # triggers GitLab's automatic recovery flow, closing the incident.
    is_recovery = state.value == "OK"

    alarm_data_dump = alarm.alarm_data.model_dump()
    # Replace reason_data strings with their parsed JSON objects when possible, so the
    # GitLab alert payload carries structured data instead of an escaped JSON string.
    alarm_data_dump["state"]["reason_data"] = maybe_parse_json(state.reason_data)
    if alarm_data_dump.get("previous_state") and alarm.alarm_data.previous_state:
        alarm_data_dump["previous_state"]["reason_data"] = maybe_parse_json(alarm.alarm_data.previous_state.reason_data)

    payload: dict[str, Any] = {
        "title": alarm.alarm_data.alarm_name,
        "description": description,
        "start_time": state.timestamp,
        "monitoring_tool": "AWS CloudWatch",
        "severity": severity,
        # fingerprint deduplicates re-firings of the same alarm in GitLab.
        "fingerprint": alarm.alarm_arn,
        # Full alarm context so every alert is self-contained and queryable.
        "alarm_url": alarm_url,
        "alarm_arn": alarm.alarm_arn,
        "region": alarm.region,
        "account_id": alarm.account_id,
        "state": state.value,
        "previous_state": previous_state_value,
        "alarm_data": alarm_data_dump,
    }

    if is_recovery:
        payload["end_time"] = state.timestamp

    if namespace := extract_metric_namespace(alarm.alarm_data):
        payload["service"] = namespace

    return payload


# --- Lambda handler ---


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    configure_logging(Env.PROD, os.environ.get("LOG_LEVEL", "INFO"))

    try:
        alarm = AlarmEvent.model_validate(event)
    except Exception:
        log.exception("alarm_action.invalid_event", raw_event=event)
        raise

    ctx_fields: dict[str, object] = {
        "lambda": "alarm_action",
        "alarm_name": alarm.alarm_data.alarm_name,
        "alarm_arn": alarm.alarm_arn,
        "region": alarm.region,
        "new_state": alarm.alarm_data.state.value,
    }
    if hasattr(context, "aws_request_id"):
        ctx_fields["aws_request_id"] = context.aws_request_id

    with structlog.contextvars.bound_contextvars(**ctx_fields):
        param_name = os.environ["GITLAB_ALERT_SSM_PARAM"]
        credentials = fetch_gitlab_credentials(param_name)

        payload = build_gitlab_payload(alarm)
        log.info("alarm_action.posting", webhook_url=credentials.webhook_url)

        try:
            response = httpx.post(
                credentials.webhook_url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {credentials.authorization_key}",
                    "Content-Type": "application/json",
                },
                timeout=_GITLAB_POST_TIMEOUT_S,
            )
        except httpx.RequestError:
            log.exception(
                "alarm_action.request_error",
                webhook_url=credentials.webhook_url,
                alarm_name=alarm.alarm_data.alarm_name,
            )
            raise

        if not response.is_success:
            log.error(
                "alarm_action.post_failed",
                status_code=response.status_code,
                response_body=response.text,
            )
            response.raise_for_status()

        log.info("alarm_action.done", alarm_name=alarm.alarm_data.alarm_name)

    return {"status": "ok", "alarm": alarm.alarm_data.alarm_name}
