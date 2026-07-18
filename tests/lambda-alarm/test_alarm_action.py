from collections.abc import Generator
from types import SimpleNamespace
from typing import Any
from unittest import mock

import httpx
import pytest
import structlog
from pydantic import ValidationError
from structlog.contextvars import merge_contextvars
from structlog.testing import capture_logs

from mitup_bot.lambdas.alarm_action import (
    GITLAB_POST_TIMEOUT_S,
    AlarmState,
    GitLabAlertCredentials,
    build_alarm_console_url,
    extract_triggering_alarms,
    handler,
)

CREDENTIALS = GitLabAlertCredentials(webhook_url="https://gitlab.example.com/alert", authorization_key="secret-token")


def metric_alarm_event() -> dict[str, Any]:
    """A realistic CloudWatch metric-alarm event (the shape CloudWatch sends to alarm-action lambdas)."""
    return {
        "source": "aws.cloudwatch",
        "alarmArn": "arn:aws:cloudwatch:eu-west-1:123456789012:alarm:bot-errors",
        "accountId": "123456789012",
        "region": "eu-west-1",
        "time": "2026-06-23T10:00:00Z",
        "alarmData": {
            "alarmName": "bot-errors",
            "state": {
                "value": "ALARM",
                "reason": "Threshold crossed: 1 datapoint greater than the threshold.",
                "reasonData": '{"version":"1.0","queryDate":"2026-06-23T10:00:00.000+0000"}',
                "timestamp": "2026-06-23T10:00:00.000+0000",
            },
            "previousState": {
                "value": "OK",
                "reason": "Threshold not crossed.",
                "timestamp": "2026-06-23T09:55:00.000+0000",
            },
            "configuration": {
                "description": "Fires when the bot logs an error.",
                "metrics": [
                    {
                        "id": "m1",
                        "metricStat": {
                            "metric": {"namespace": "Mitup/Bot", "name": "Fault"},
                            "period": 60,
                            "stat": "Sum",
                        },
                        "returnData": True,
                    }
                ],
            },
        },
    }


def composite_alarm_event() -> dict[str, Any]:
    """A CloudWatch composite-alarm event whose reasonData names the triggering child alarms."""
    return {
        "source": "aws.cloudwatch",
        "alarmArn": "arn:aws:cloudwatch:eu-west-1:123456789012:alarm:MitupEventFault",
        "accountId": "123456789012",
        "region": "eu-west-1",
        "time": "2026-07-15T21:11:41Z",
        "alarmData": {
            "alarmName": "MitupEventFault",
            "state": {
                "value": "ALARM",
                "reason": "arn:aws:cloudwatch:...:alarm:MitupEventFault transitioned to ALARM",
                "reasonData": (
                    '{"triggeringAlarms":[{"arn":'
                    '"arn:aws:cloudwatch:eu-west-1:123456789012:alarm:MitupEventFault-DeactivateMeetings",'
                    '"state":{"value":"ALARM"}}]}'
                ),
                "timestamp": "2026-07-15T21:11:41.875+0000",
            },
            "previousState": {
                "value": "OK",
                "timestamp": "2026-07-15T20:55:41.000+0000",
            },
            "configuration": {"description": "Composite alarm for event-processing faults."},
        },
    }


def ok_response() -> mock.MagicMock:
    response = mock.MagicMock(name="httpx.Response")
    response.is_success = True
    response.status_code = 200
    return response


@pytest.fixture(autouse=True)
def mock_configure_logging() -> Generator[mock.MagicMock]:
    """Mock `configure_logging` for every test in this module.

    `configure_logging` mutates process-global root logging state via `basicConfig`. Mocking it
    keeps the lambda tests hermetic.
    """
    with mock.patch("mitup_bot.lambdas.alarm_action.configure_logging") as mocked:
        yield mocked


@pytest.fixture(autouse=True)
def gitlab_ssm_param(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GITLAB_ALERT_SSM_PARAM", "some/param")


@pytest.fixture
def mock_fetch_credentials() -> Generator[mock.MagicMock]:
    with mock.patch(
        "mitup_bot.lambdas.alarm_action.fetch_gitlab_credentials",
        return_value=CREDENTIALS,
    ) as fetch:
        yield fetch


@pytest.fixture
def mock_post() -> Generator[mock.MagicMock]:
    with mock.patch("mitup_bot.lambdas.alarm_action.httpx.post", return_value=ok_response()) as post:
        yield post


def test_happy_path_posts_critical_alert(
    mock_fetch_credentials: mock.MagicMock,
    mock_post: mock.MagicMock,
):
    """A metric-alarm event in ALARM state posts a critical GitLab alert with bearer auth."""
    result = handler(metric_alarm_event(), None)

    mock_post.assert_called_once()
    _args, kwargs = mock_post.call_args
    assert mock_post.call_args.args[0] == CREDENTIALS.webhook_url
    assert kwargs["headers"]["Authorization"] == "Bearer secret-token"  # f"Bearer {authorization_key}"
    assert kwargs["headers"]["Content-Type"] == "application/json"
    assert kwargs["timeout"] == GITLAB_POST_TIMEOUT_S

    payload = kwargs["json"]
    assert payload["title"] == "bot-errors"  # alarmName
    assert payload["severity"] == "critical"  # state value ALARM
    assert payload["monitoring_tool"] == "AWS CloudWatch"
    assert payload["state"] == "ALARM"
    assert payload["previous_state"] == "OK"
    assert payload["state_transitioned_at"] == "2026-06-23T10:00:00.000+0000"  # state.timestamp
    assert payload["service"] == "Mitup/Bot"  # configuration.metrics[].metricStat.metric.namespace
    assert payload["fingerprint"] == "arn:aws:cloudwatch:eu-west-1:123456789012:alarm:bot-errors"  # alarmArn
    assert "end_time" not in payload  # end_time is only set on recovery (state == "OK")

    assert result == {"status": "ok", "alarm": "bot-errors"}


def test_payload_omits_raw_alarm_data(
    mock_fetch_credentials: mock.MagicMock,
    mock_post: mock.MagicMock,
):
    """The curated payload never carries the raw alarm_data dump GitLab would flatten into noise."""
    handler(metric_alarm_event(), None)

    payload = mock_post.call_args.kwargs["json"]
    assert "alarm_data" not in payload


def test_gitlab_environment_name_present(
    mock_fetch_credentials: mock.MagicMock,
    mock_post: mock.MagicMock,
):
    """Every payload tags the GitLab production environment so alerts route to the right monitor."""
    handler(metric_alarm_event(), None)

    payload = mock_post.call_args.kwargs["json"]
    assert payload["gitlab_environment_name"] == "production"


def test_raw_event_logged_at_info(
    mock_fetch_credentials: mock.MagicMock,
    mock_post: mock.MagicMock,
):
    """The handler logs the validated event once so the full context stays queryable in the log group."""
    event = metric_alarm_event()

    with capture_logs(processors=[merge_contextvars]) as logs:
        handler(event, None)

    received = [entry for entry in logs if entry["event"] == "Alarm event received"]
    assert len(received) == 1
    assert received[0]["raw_event"] == event


def test_metric_alarm_keeps_reason_in_description(
    mock_fetch_credentials: mock.MagicMock,
    mock_post: mock.MagicMock,
):
    """A metric alarm keeps CloudWatch's Threshold-Crossed reason as the description headline."""
    handler(metric_alarm_event(), None)

    description = mock_post.call_args.kwargs["json"]["description"]
    assert description.startswith("Threshold crossed: 1 datapoint greater than the threshold.")
    # The console link and the operator-facing configuration description both appear.
    assert f"[Open alarm in CloudWatch]({build_alarm_console_url('eu-west-1', 'bot-errors')})" in description
    assert "Fires when the bot logs an error." in description
    # The state-transition line renders both states and the sub-second-trimmed timestamp.
    assert "**State:** OK → ALARM at 2026-06-23T10:00:00+0000" in description


def test_configuration_description_surfaced_in_description_and_field(
    mock_fetch_credentials: mock.MagicMock,
    mock_post: mock.MagicMock,
):
    """configuration.description feeds both the Markdown body and the alarm_description custom field."""
    handler(metric_alarm_event(), None)

    payload = mock_post.call_args.kwargs["json"]
    assert payload["alarm_description"] == "Fires when the bot logs an error."
    assert "Fires when the bot logs an error." in payload["description"]


def test_configuration_description_omitted_when_absent(
    mock_fetch_credentials: mock.MagicMock,
    mock_post: mock.MagicMock,
):
    """With no configuration.description, the alarm_description custom field is omitted entirely."""
    event = metric_alarm_event()
    del event["alarmData"]["configuration"]["description"]

    handler(event, None)

    assert "alarm_description" not in mock_post.call_args.kwargs["json"]


def test_composite_event_extracts_triggering_alarms(
    mock_fetch_credentials: mock.MagicMock,
    mock_post: mock.MagicMock,
):
    """A composite alarm names its triggering children in the headline, the links, and a custom field."""
    handler(composite_alarm_event(), None)

    payload = mock_post.call_args.kwargs["json"]
    assert payload["triggering_alarms"] == ["MitupEventFault-DeactivateMeetings"]

    description = payload["description"]
    assert description.startswith("Triggered by: **MitupEventFault-DeactivateMeetings**")
    child_url = build_alarm_console_url("eu-west-1", "MitupEventFault-DeactivateMeetings")
    assert f"[Open MitupEventFault-DeactivateMeetings in CloudWatch]({child_url})" in description


def test_recovery_description_includes_duration_and_end_time(
    mock_fetch_credentials: mock.MagicMock,
    mock_post: mock.MagicMock,
):
    """A recovery headline reports the outage duration and the payload sets end_time for auto-resolve."""
    event = metric_alarm_event()
    event["alarmData"]["state"]["value"] = "OK"
    event["alarmData"]["previousState"]["value"] = "ALARM"

    handler(event, None)

    payload = mock_post.call_args.kwargs["json"]
    # 09:55 → 10:00 is a 5-minute outage.
    assert payload["description"].startswith("Alarm recovered after 5m.")
    assert payload["end_time"] == "2026-06-23T10:00:00.000+0000"  # state.timestamp
    assert payload["severity"] == "info"  # non-ALARM state
    assert "**State:** ALARM → OK at 2026-06-23T10:00:00+0000" in payload["description"]


def test_recovery_without_parseable_timestamps_omits_duration(
    mock_fetch_credentials: mock.MagicMock,
    mock_post: mock.MagicMock,
):
    """When the timestamps don't parse, the recovery headline drops the duration but never crashes."""
    event = metric_alarm_event()
    event["alarmData"]["state"]["value"] = "OK"
    event["alarmData"]["state"]["timestamp"] = "not a timestamp"

    handler(event, None)

    payload = mock_post.call_args.kwargs["json"]
    assert payload["description"].startswith("Alarm recovered.")
    assert "after" not in payload["description"].splitlines()[0]


def test_malformed_reason_data_yields_no_triggering_alarms(
    mock_fetch_credentials: mock.MagicMock,
    mock_post: mock.MagicMock,
):
    """Non-JSON reasonData is tolerated: no crash and no triggering_alarms custom field."""
    event = metric_alarm_event()
    event["alarmData"]["state"]["reasonData"] = "not json"

    result = handler(event, None)

    payload = mock_post.call_args.kwargs["json"]
    assert "triggering_alarms" not in payload
    assert result == {"status": "ok", "alarm": "bot-errors"}


def test_non_alarm_state_defaults_severity_to_info(
    mock_fetch_credentials: mock.MagicMock,
    mock_post: mock.MagicMock,
):
    """Any state other than ALARM yields the generic "info" severity."""
    event = metric_alarm_event()
    event["alarmData"]["state"]["value"] = "OK"

    handler(event, None)

    payload = mock_post.call_args.kwargs["json"]
    assert payload["severity"] == "info"  # non-ALARM state
    assert payload["state"] == "OK"


def test_ok_state_sets_end_time_for_recovery(
    mock_fetch_credentials: mock.MagicMock,
    mock_post: mock.MagicMock,
):
    """A recovery (state == "OK") includes end_time so GitLab auto-resolves the matching alert.

    GitLab matches the open alert by fingerprint, so the fingerprint must still equal the alarm ARN.
    """
    event = metric_alarm_event()
    event["alarmData"]["state"]["value"] = "OK"

    handler(event, None)

    payload = mock_post.call_args.kwargs["json"]
    assert payload["end_time"] == "2026-06-23T10:00:00.000+0000"  # state.timestamp
    assert payload["start_time"] == "2026-06-23T10:00:00.000+0000"  # state.timestamp (always set)
    assert payload["fingerprint"] == "arn:aws:cloudwatch:eu-west-1:123456789012:alarm:bot-errors"  # alarmArn
    assert payload["severity"] == "info"  # non-ALARM state


def test_insufficient_data_state_omits_end_time(
    mock_fetch_credentials: mock.MagicMock,
    mock_post: mock.MagicMock,
):
    """INSUFFICIENT_DATA is not a recovery, so end_time is absent from the payload."""
    event = metric_alarm_event()
    event["alarmData"]["state"]["value"] = "INSUFFICIENT_DATA"

    handler(event, None)

    payload = mock_post.call_args.kwargs["json"]
    assert "end_time" not in payload  # end_time is only set on recovery (state == "OK")
    assert payload["state"] == "INSUFFICIENT_DATA"


def test_non_2xx_response_propagates(
    mock_fetch_credentials: mock.MagicMock,
):
    """A non-2xx GitLab response triggers raise_for_status, which propagates out of the handler."""
    failing_response = mock.MagicMock(name="httpx.Response")
    failing_response.is_success = False
    failing_response.status_code = 500
    failing_response.text = "boom"
    failing_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Server error", request=mock.MagicMock(), response=failing_response
    )

    with mock.patch("mitup_bot.lambdas.alarm_action.httpx.post", return_value=failing_response):
        with pytest.raises(httpx.HTTPStatusError):
            handler(metric_alarm_event(), None)


def test_recovery_400_is_tolerated(
    mock_fetch_credentials: mock.MagicMock,
):
    """A 400 to a recovery post (state == "OK") means GitLab has no open alert to resolve: the
    handler logs a warning and completes successfully instead of raising."""
    no_open_alert_response = mock.MagicMock(name="httpx.Response")
    no_open_alert_response.is_success = False
    no_open_alert_response.status_code = 400
    no_open_alert_response.text = ""

    event = metric_alarm_event()
    event["alarmData"]["state"]["value"] = "OK"

    with mock.patch("mitup_bot.lambdas.alarm_action.httpx.post", return_value=no_open_alert_response):
        with capture_logs(processors=[merge_contextvars]) as logs:
            result = handler(event, None)

    no_open_alert_response.raise_for_status.assert_not_called()
    assert result == {"status": "ok", "alarm": "bot-errors"}

    warnings = [log for log in logs if log["event"] == "GitLab has no open alert to resolve for this recovery"]
    assert len(warnings) == 1
    assert warnings[0]["log_level"] == "warning"
    assert warnings[0]["status_code"] == 400


def test_alarm_400_still_propagates(
    mock_fetch_credentials: mock.MagicMock,
):
    """A 400 to an ALARM-state post is a genuine failure and still raises via raise_for_status."""
    failing_response = mock.MagicMock(name="httpx.Response")
    failing_response.is_success = False
    failing_response.status_code = 400
    failing_response.text = "bad request"
    failing_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Bad request", request=mock.MagicMock(), response=failing_response
    )

    with mock.patch("mitup_bot.lambdas.alarm_action.httpx.post", return_value=failing_response):
        with pytest.raises(httpx.HTTPStatusError):
            handler(metric_alarm_event(), None)


def test_request_error_propagates(
    mock_fetch_credentials: mock.MagicMock,
    mock_post: mock.MagicMock,
):
    """A transport-level failure logs "GitLab request failed" and re-raises the httpx.RequestError."""
    mock_post.side_effect = httpx.RequestError("network error", request=mock.Mock())

    with capture_logs(processors=[merge_contextvars]) as logs:
        with pytest.raises(httpx.RequestError):
            handler(metric_alarm_event(), None)

    request_error_logs = [log for log in logs if log["event"] == "GitLab request failed"]
    assert len(request_error_logs) == 1


def test_handler_raises_when_ssm_param_env_missing(
    monkeypatch: pytest.MonkeyPatch,
):
    """The handler reads os.environ["GITLAB_ALERT_SSM_PARAM"] directly, so a missing var raises KeyError."""
    monkeypatch.delenv("GITLAB_ALERT_SSM_PARAM", raising=False)

    with pytest.raises(KeyError):
        handler(metric_alarm_event(), None)


def test_invalid_event_raises_and_logs(
    mock_fetch_credentials: mock.MagicMock,
    mock_post: mock.MagicMock,
):
    """An event missing the required alarmData fails validation, logs the invalid event, and raises."""
    event = metric_alarm_event()
    del event["alarmData"]

    with capture_logs(processors=[merge_contextvars]) as logs:
        with pytest.raises(ValidationError):
            handler(event, None)

    invalid_logs = [log for log in logs if log["event"] == "Invalid alarm event"]
    assert len(invalid_logs) == 1
    mock_post.assert_not_called()


def test_binds_invocation_contextvars_during_handler_body(
    mock_fetch_credentials: mock.MagicMock,
    mock_post: mock.MagicMock,
):
    """The handler binds alarm metadata for the duration of the body, so logs emitted while posting
    carry the invocation context."""
    with capture_logs(processors=[merge_contextvars]) as logs:
        handler(metric_alarm_event(), None)

    posting = [log for log in logs if log["event"] == "Posting alert to GitLab"]
    assert len(posting) == 1
    entry = posting[0]
    assert entry["flow"] == "alarm_action"
    assert "lambda" not in entry
    assert entry["alarm_name"] == "bot-errors"
    assert entry["alarm_arn"] == "arn:aws:cloudwatch:eu-west-1:123456789012:alarm:bot-errors"
    assert entry["region"] == "eu-west-1"
    assert entry["new_state"] == "ALARM"


def test_includes_aws_request_id_when_context_has_it(
    mock_fetch_credentials: mock.MagicMock,
    mock_post: mock.MagicMock,
):
    """When the AWS context arg exposes aws_request_id, it is bound alongside the other fields."""
    context = SimpleNamespace(aws_request_id="req-abc")

    with capture_logs(processors=[merge_contextvars]) as logs:
        handler(metric_alarm_event(), context)

    entry = next(log for log in logs if log["event"] == "Posting alert to GitLab")
    assert entry["aws_request_id"] == "req-abc"


def test_omits_aws_request_id_when_context_lacks_it(
    mock_fetch_credentials: mock.MagicMock,
    mock_post: mock.MagicMock,
):
    """The hasattr guard omits aws_request_id when the context arg doesn't carry one (e.g. None)."""
    with capture_logs(processors=[merge_contextvars]) as logs:
        handler(metric_alarm_event(), None)

    entry = next(log for log in logs if log["event"] == "Posting alert to GitLab")
    assert "aws_request_id" not in entry


def test_clears_invocation_contextvars_after_return(
    mock_fetch_credentials: mock.MagicMock,
    mock_post: mock.MagicMock,
):
    """bound_contextvars auto-clears on exit, so a log emitted after the handler returns carries
    none of the invocation fields."""
    with capture_logs(processors=[merge_contextvars]) as logs:
        handler(metric_alarm_event(), None)
        structlog.get_logger("mitup_bot").info("after handler")

    entry = next(log for log in logs if log["event"] == "after handler")
    for field in ("flow", "alarm_name", "alarm_arn", "region", "new_state", "aws_request_id"):
        assert field not in entry


def test_tolerant_payload_without_optional_blocks(
    mock_fetch_credentials: mock.MagicMock,
    mock_post: mock.MagicMock,
):
    """A composite/log-style event that omits configuration, previousState and reasonData still
    validates and posts (previous_state collapses to None)."""
    event = metric_alarm_event()
    del event["alarmData"]["previousState"]
    del event["alarmData"]["configuration"]
    del event["alarmData"]["state"]["reasonData"]

    result = handler(event, None)

    payload = mock_post.call_args.kwargs["json"]
    assert payload["previous_state"] is None
    # With no previous state the transition line still renders, defaulting to "unknown".
    assert "**State:** unknown → ALARM" in payload["description"]
    assert result == {"status": "ok", "alarm": "bot-errors"}


def test_service_omitted_when_no_metric_namespace(
    mock_fetch_credentials: mock.MagicMock,
    mock_post: mock.MagicMock,
):
    """A metric-math configuration (no metricStat namespace) yields no derivable service, so the key
    is omitted from the payload entirely."""
    event = metric_alarm_event()
    event["alarmData"]["configuration"]["metrics"] = [{"id": "e1", "expression": "m1 + m2"}]

    handler(event, None)

    payload = mock_post.call_args.kwargs["json"]
    assert "service" not in payload


def test_missing_reason_and_timestamp_fall_back(
    mock_fetch_credentials: mock.MagicMock,
    mock_post: mock.MagicMock,
):
    """When the alarm state omits reason and timestamp, the description uses its generic headline
    fallback and start_time is None."""
    event = metric_alarm_event()
    event["alarmData"]["state"].pop("reason", None)
    event["alarmData"]["state"].pop("timestamp", None)

    handler(event, None)

    payload = mock_post.call_args.kwargs["json"]
    assert payload["description"].startswith("CloudWatch alarm state changed")  # headline fallback
    # The console link is present even on the fallback path.
    assert payload["alarm_url"] == build_alarm_console_url("eu-west-1", "bot-errors")
    assert f"[Open alarm in CloudWatch]({payload['alarm_url']})" in payload["description"]
    assert payload["start_time"] is None
    # With no timestamp the state line drops the "at ..." suffix.
    assert "**State:** OK → ALARM" in payload["description"]


def test_payload_includes_alarm_console_url(
    mock_fetch_credentials: mock.MagicMock,
    mock_post: mock.MagicMock,
):
    """The payload carries a CloudWatch console deep link both as alarm_url and inside the
    description, so the link survives into GitLab's downstream notification fanout."""
    handler(metric_alarm_event(), None)

    payload = mock_post.call_args.kwargs["json"]
    # Derived via build_alarm_console_url so the URL format lives in one place; the format
    # itself is pinned by test_build_alarm_console_url_encodes_name_and_region below.
    expected_url = build_alarm_console_url("eu-west-1", "bot-errors")
    assert payload["alarm_url"] == expected_url
    assert f"[Open alarm in CloudWatch]({expected_url})" in payload["description"]


@pytest.mark.parametrize(
    ("reason_data", "expected"),
    [
        (
            '{"triggeringAlarms":[{"arn":"arn:aws:cloudwatch:eu-west-1:1:alarm:Child-A"},'
            '{"arn":"arn:aws:cloudwatch:eu-west-1:1:alarm:Child-B"}]}',
            ["Child-A", "Child-B"],
        ),
        ("not json", []),  # non-JSON reasonData
        ('{"version":"1.0"}', []),  # JSON without triggeringAlarms
        ('{"triggeringAlarms":"nope"}', []),  # triggeringAlarms not a list
        ('{"triggeringAlarms":[{"state":{"value":"ALARM"}}]}', []),  # entry without arn
        ('{"triggeringAlarms":[{"arn":"no-alarm-segment"}]}', []),  # arn missing :alarm: segment
        (None, []),  # absent reasonData
    ],
)
def test_extract_triggering_alarms(reason_data: str | None, expected: list[str]):
    """extract_triggering_alarms names every child arn defensively, never raising on bad input."""
    state = AlarmState(value="ALARM", reasonData=reason_data)
    assert extract_triggering_alarms(state) == expected


@pytest.mark.parametrize(
    ("region", "alarm_name", "expected_url"),
    [
        (
            "eu-west-1",
            "bot-errors",
            "https://eu-west-1.console.aws.amazon.com/cloudwatch/home?region=eu-west-1#alarmsV2:alarm/bot-errors",
        ),
        (
            "eu-west-1",
            "My alarm / with spaces",
            "https://eu-west-1.console.aws.amazon.com/cloudwatch/home"
            "?region=eu-west-1#alarmsV2:alarm/My%20alarm%20%2F%20with%20spaces",
        ),
        (
            "us-east-1",
            "error%rate?",
            "https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#alarmsV2:alarm/error%25rate%3F",
        ),
    ],
)
def test_build_alarm_console_url_encodes_name_and_region(region: str, alarm_name: str, expected_url: str):
    """build_alarm_console_url URL-encodes the alarm name (spaces, slashes, percent, query chars)
    and interpolates the region in both the console hostname and the region query parameter."""
    assert build_alarm_console_url(region, alarm_name) == expected_url
