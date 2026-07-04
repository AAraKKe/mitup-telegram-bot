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
    GitLabAlertCredentials,
    build_alarm_console_url,
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
    assert payload["service"] == "Mitup/Bot"  # configuration.metrics[].metricStat.metric.namespace
    assert payload["fingerprint"] == "arn:aws:cloudwatch:eu-west-1:123456789012:alarm:bot-errors"  # alarmArn
    assert "end_time" not in payload  # end_time is only set on recovery (state == "OK")

    # reasonData is a JSON string on the wire but the payload carries it parsed into a dict.
    parsed_reason_data = payload["alarm_data"]["state"]["reason_data"]
    assert isinstance(parsed_reason_data, dict)
    assert parsed_reason_data["version"] == "1.0"

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


def test_request_error_propagates(
    mock_fetch_credentials: mock.MagicMock,
    mock_post: mock.MagicMock,
):
    """A transport-level failure logs alarm_action.request_error and re-raises the httpx.RequestError."""
    mock_post.side_effect = httpx.RequestError("network error", request=mock.Mock())

    with capture_logs(processors=[merge_contextvars]) as logs:
        with pytest.raises(httpx.RequestError):
            handler(metric_alarm_event(), None)

    request_error_logs = [log for log in logs if log["event"] == "alarm_action.request_error"]
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

    invalid_logs = [log for log in logs if log["event"] == "alarm_action.invalid_event"]
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

    posting = [log for log in logs if log["event"] == "alarm_action.posting"]
    assert len(posting) == 1
    entry = posting[0]
    assert entry["lambda"] == "alarm_action"
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

    entry = next(log for log in logs if log["event"] == "alarm_action.posting")
    assert entry["aws_request_id"] == "req-abc"


def test_omits_aws_request_id_when_context_lacks_it(
    mock_fetch_credentials: mock.MagicMock,
    mock_post: mock.MagicMock,
):
    """The hasattr guard omits aws_request_id when the context arg doesn't carry one (e.g. None)."""
    with capture_logs(processors=[merge_contextvars]) as logs:
        handler(metric_alarm_event(), None)

    entry = next(log for log in logs if log["event"] == "alarm_action.posting")
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
    for field in ("lambda", "alarm_name", "alarm_arn", "region", "new_state", "aws_request_id"):
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
    assert result == {"status": "ok", "alarm": "bot-errors"}


def test_reason_data_parsed_in_payload(
    mock_fetch_credentials: mock.MagicMock,
    mock_post: mock.MagicMock,
):
    """A JSON-string reasonData is parsed into a structured object in the posted payload."""
    event = metric_alarm_event()
    event["alarmData"]["state"]["reasonData"] = '{"version":"1.0","threshold":5}'

    handler(event, None)

    payload = mock_post.call_args.kwargs["json"]
    parsed_reason_data = payload["alarm_data"]["state"]["reason_data"]
    assert parsed_reason_data == {"version": "1.0", "threshold": 5}


def test_reason_data_left_as_string_when_not_json(
    mock_fetch_credentials: mock.MagicMock,
    mock_post: mock.MagicMock,
):
    """maybe_parse_json never raises: a non-JSON reasonData is forwarded verbatim as the original string."""
    event = metric_alarm_event()
    event["alarmData"]["state"]["reasonData"] = "not json"

    handler(event, None)

    payload = mock_post.call_args.kwargs["json"]
    assert payload["alarm_data"]["state"]["reason_data"] == "not json"


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
    """When the alarm state omits reason and timestamp, build_gitlab_payload uses its generic fallback
    description and a None start_time."""
    event = metric_alarm_event()
    event["alarmData"]["state"].pop("reason", None)
    event["alarmData"]["state"].pop("timestamp", None)

    handler(event, None)

    payload = mock_post.call_args.kwargs["json"]
    assert payload["description"].startswith("CloudWatch alarm state changed")  # build_gitlab_payload fallback
    # The console link is present even on the fallback path.
    assert payload["alarm_url"] == build_alarm_console_url("eu-west-1", "bot-errors")
    assert payload["description"].endswith(f"Alarm: {payload['alarm_url']}")
    assert payload["start_time"] is None


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
    assert payload["description"].endswith(f"Alarm: {expected_url}")
    # The original CloudWatch reason still leads the description.
    assert payload["description"].startswith("Threshold crossed: 1 datapoint greater than the threshold.")


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
