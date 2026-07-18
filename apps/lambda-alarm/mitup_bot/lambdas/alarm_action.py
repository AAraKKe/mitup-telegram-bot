"""Lambda entry point for CloudWatch alarm actions.

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
from datetime import datetime
from typing import Any
from urllib.parse import quote

import boto3
import httpx
import structlog
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from mitup_bot.config import Env
from mitup_bot.logging_config import Component, configure_logging

log = structlog.get_logger(__name__)

# Timeout applied to the GitLab HTTP endpoint POST request.
GITLAB_POST_TIMEOUT_S = 10


# --- Pydantic models for the CloudWatch alarm event ---


class AlarmState(BaseModel):
    # CloudWatch sends camelCase keys (e.g. "reasonData"); alias_generator maps them to snake_case.
    model_config = ConfigDict(extra="allow", alias_generator=to_camel, populate_by_name=True)

    value: str
    reason: str | None = None
    reason_data: str | None = None  # JSON string; parsed on demand, not by pydantic
    timestamp: str | None = None


class AlarmPreviousState(BaseModel):
    model_config = ConfigDict(extra="allow", alias_generator=to_camel, populate_by_name=True)

    value: str | None = None
    reason: str | None = None
    reason_data: str | None = None  # JSON string; parsed on demand, not by pydantic
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


# --- SSM credential fetch ---


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


def alarm_name_from_arn(arn: str) -> str | None:
    """Return the alarm name (the ARN segment after ``:alarm:``), or None when absent."""
    if ":alarm:" not in arn:
        return None
    return arn.split(":alarm:", 1)[1]


def extract_triggering_alarms(state: AlarmState) -> list[str]:
    """Return the names of the child alarms that fired a composite alarm.

    Composite alarms carry a ``triggeringAlarms`` list inside the JSON-string ``reason_data``;
    each entry's name is the ARN segment after ``:alarm:``. Never raises — non-composite or
    malformed payloads yield an empty list.
    """
    parsed = maybe_parse_json(state.reason_data)
    if not isinstance(parsed, dict):
        return []
    triggering = parsed.get("triggeringAlarms")
    if not isinstance(triggering, list):
        return []
    names = (
        alarm_name_from_arn(entry["arn"])
        for entry in triggering
        if isinstance(entry, dict) and isinstance(entry.get("arn"), str)
    )
    return [name for name in names if name is not None]


def parse_cloudwatch_timestamp(value: str | None) -> datetime | None:
    """Parse a CloudWatch timestamp (e.g. ``2026-07-15T21:11:41.875+0000``); None on failure."""
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def format_transition_timestamp(value: str | None) -> str | None:
    """Render a CloudWatch timestamp without sub-second noise; fall back to the raw value."""
    parsed = parse_cloudwatch_timestamp(value)
    if parsed is None:
        return value
    return parsed.strftime("%Y-%m-%dT%H:%M:%S%z")


def format_outage_duration(previous: str | None, current: str | None) -> str | None:
    """Human-readable gap between two CloudWatch timestamps (e.g. ``16m``); None when unparseable."""
    start = parse_cloudwatch_timestamp(previous)
    end = parse_cloudwatch_timestamp(current)
    if start is None or end is None:
        return None
    seconds = int((end - start).total_seconds())
    if seconds < 0:
        return None
    if seconds < 60:
        return f"{seconds}s"
    minutes, _remaining_seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, remaining_minutes = divmod(minutes, 60)
    return f"{hours}h {remaining_minutes}m"


def configuration_description(alarm_data: AlarmData) -> str | None:
    """Return the operator-facing alarm description from the terraform configuration, if any."""
    if alarm_data.configuration is None:
        return None
    description = alarm_data.configuration.get("description")
    return str(description) if description else None


def build_headline(state: AlarmState, previous_timestamp: str | None, triggering_alarms: list[str]) -> str:
    """First description line: a recovery notice, the triggering child alarms, or the raw reason."""
    if state.value == "OK":
        duration = format_outage_duration(previous_timestamp, state.timestamp)
        return f"Alarm recovered after {duration}." if duration else "Alarm recovered."
    if triggering_alarms:
        names = ", ".join(f"**{name}**" for name in triggering_alarms)
        return f"Triggered by: {names}"
    return state.reason or "CloudWatch alarm state changed"


def build_state_line(state: AlarmState, previous_state: AlarmPreviousState | None) -> str:
    previous_value = previous_state.value if previous_state else None
    transition = f"{previous_value or 'unknown'} → {state.value}"
    if formatted_timestamp := format_transition_timestamp(state.timestamp):
        return f"**State:** {transition} at {formatted_timestamp}"
    return f"**State:** {transition}"


def build_console_links(region: str, alarm_url: str, triggering_alarms: list[str]) -> list[str]:
    return [
        f"[Open alarm in CloudWatch]({alarm_url})",
        *(f"[Open {name} in CloudWatch]({build_alarm_console_url(region, name)})" for name in triggering_alarms),
    ]


def build_description(alarm: AlarmEvent, alarm_url: str, triggering_alarms: list[str]) -> str:
    """Build the Markdown description GitLab renders on the incident and forwards downstream.

    The description is the only field that reliably survives into GitLab's Telegram/email
    fanout, so it carries the operator-facing context: what fired, why, the state transition,
    and console deep links.
    """
    state = alarm.alarm_data.state
    previous_state = alarm.alarm_data.previous_state
    previous_timestamp = previous_state.timestamp if previous_state else None

    paragraphs = [build_headline(state, previous_timestamp, triggering_alarms)]
    if alarm_description := configuration_description(alarm.alarm_data):
        paragraphs.append(alarm_description)
    paragraphs.append(build_state_line(state, previous_state))
    paragraphs.extend(build_console_links(alarm.region, alarm_url, triggering_alarms))
    return "\n\n".join(paragraphs)


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
    previous_state = alarm.alarm_data.previous_state
    previous_state_value = previous_state.value if previous_state else None

    triggering_alarms = extract_triggering_alarms(state)
    alarm_url = build_alarm_console_url(alarm.region, alarm.alarm_data.alarm_name)
    severity = "critical" if state.value == "ALARM" else "info"

    payload: dict[str, Any] = {
        "title": alarm.alarm_data.alarm_name,
        "description": build_description(alarm, alarm_url, triggering_alarms),
        "start_time": state.timestamp,
        "monitoring_tool": "AWS CloudWatch",
        "severity": severity,
        # fingerprint deduplicates re-firings of the same alarm in GitLab.
        "fingerprint": alarm.alarm_arn,
        "gitlab_environment_name": "production",
        # Curated flat context — GitLab dumps every non-native field on the alert-details page.
        "alarm_url": alarm_url,
        "alarm_arn": alarm.alarm_arn,
        "region": alarm.region,
        "account_id": alarm.account_id,
        "state": state.value,
        "previous_state": previous_state_value,
        "state_transitioned_at": state.timestamp,
    }

    if alarm_description := configuration_description(alarm.alarm_data):
        payload["alarm_description"] = alarm_description
    if triggering_alarms:
        payload["triggering_alarms"] = triggering_alarms
    # end_time on a payload whose fingerprint matches an open alert triggers GitLab's recovery flow.
    if state.value == "OK":
        payload["end_time"] = state.timestamp
    if namespace := extract_metric_namespace(alarm.alarm_data):
        payload["service"] = namespace

    return payload


# --- Lambda handler ---


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    configure_logging(Env.PROD, Component.LAMBDA, os.environ.get("LOG_LEVEL", "INFO"))

    try:
        alarm = AlarmEvent.model_validate(event)
    except Exception:
        log.exception("Invalid alarm event", raw_event=event)
        raise

    ctx_fields: dict[str, object] = {
        "flow": "alarm_action",
        "alarm_name": alarm.alarm_data.alarm_name,
        "alarm_arn": alarm.alarm_arn,
        "region": alarm.region,
        "new_state": alarm.alarm_data.state.value,
    }
    if hasattr(context, "aws_request_id"):
        ctx_fields["aws_request_id"] = context.aws_request_id

    with structlog.contextvars.bound_contextvars(**ctx_fields):
        # The payload carries only curated fields, so log the raw event to keep it queryable here.
        log.info("Alarm event received", raw_event=event)
        param_name = os.environ["GITLAB_ALERT_SSM_PARAM"]
        credentials = fetch_gitlab_credentials(param_name)

        payload = build_gitlab_payload(alarm)
        log.info("Posting alert to GitLab", webhook_url=credentials.webhook_url)

        try:
            response = httpx.post(
                credentials.webhook_url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {credentials.authorization_key}",
                    "Content-Type": "application/json",
                },
                timeout=GITLAB_POST_TIMEOUT_S,
            )
        except httpx.RequestError:
            log.exception(
                "GitLab request failed",
                webhook_url=credentials.webhook_url,
                alarm_name=alarm.alarm_data.alarm_name,
            )
            raise

        if not response.is_success:
            is_recovery = alarm.alarm_data.state.value == "OK"
            if is_recovery and response.status_code == 400:
                # GitLab returns 400 when a recovery post carries no matching open alert to resolve
                # (the alarm went straight to OK without ever firing). This is benign — nothing to do.
                log.warning(
                    "GitLab has no open alert to resolve for this recovery",
                    status_code=response.status_code,
                    response_body=response.text,
                )
            else:
                log.error(
                    "GitLab alert post failed",
                    status_code=response.status_code,
                    response_body=response.text,
                )
                response.raise_for_status()

        log.info("Alarm action completed", alarm_name=alarm.alarm_data.alarm_name)

    return {"status": "ok", "alarm": alarm.alarm_data.alarm_name}
