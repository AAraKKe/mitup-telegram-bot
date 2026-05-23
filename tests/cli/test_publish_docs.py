import os
from unittest.mock import MagicMock, call, patch

import pytest
from click.exceptions import Abort
from click.testing import CliRunner

from mitup_bot.cli.commands.publish_docs import (
    ASSET_CACHE_CONTROL,
    HTML_CACHE_CONTROL,
    get_distribution_id,
    s3_sync,
)
from mitup_bot.cli.commands.publish_docs import cli as publish_docs_cli

MODULE_PATH = "mitup_bot.cli.commands.publish_docs"


@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch: pytest.MonkeyPatch):
    """Automatically mock environment variables for all tests."""
    monkeypatch.setenv("BOT_DOMAIN", "test.domain.com")
    monkeypatch.setenv("CI_COMMIT_SHORT_SHA", "testsha123")


@pytest.fixture
def runner():
    """Provides a CliRunner instance."""
    return CliRunner()


@patch(f"{MODULE_PATH}.sleep", return_value=None)
@patch(f"{MODULE_PATH}.success")
@patch(f"{MODULE_PATH}.error")
@patch(f"{MODULE_PATH}.console")
@patch(f"{MODULE_PATH}.boto3.client")
@patch(f"{MODULE_PATH}.s3_sync")
def test_publish_docs_invalidates_everything(
    mock_s3_sync: MagicMock,
    mock_boto_client: MagicMock,
    mock_console: MagicMock,
    mock_error: MagicMock,
    mock_success: MagicMock,
    mock_sleep: MagicMock,
    runner: CliRunner,
):
    """Every publish-docs run invalidates `/*` once the sync has completed."""
    mock_dist_id = "EXAMPLE12345"
    mock_invalidation_id = "INVALIDATION_ID_67890"
    mock_caller_ref = f"mitup-ci-{os.environ['CI_COMMIT_SHORT_SHA']}"

    mock_cf_client = MagicMock()
    mock_boto_client.return_value = mock_cf_client
    mock_cf_client.list_distributions.return_value = {
        "ResponseMetadata": {"HTTPStatusCode": 200},
        "DistributionList": {"Items": [{"Id": mock_dist_id}]},
    }
    mock_cf_client.create_invalidation.return_value = {
        "ResponseMetadata": {"HTTPStatusCode": 201},
        "Invalidation": {"Id": mock_invalidation_id},
    }
    mock_cf_client.get_invalidation.side_effect = [
        {
            "ResponseMetadata": {"HTTPStatusCode": 200},
            "Invalidation": {"Status": "InProgress", "Id": mock_invalidation_id},
        },
        {
            "ResponseMetadata": {"HTTPStatusCode": 200},
            "Invalidation": {"Status": "Completed", "Id": mock_invalidation_id},
        },
    ]

    result = runner.invoke(publish_docs_cli)

    assert result.exit_code == 0, f"Expected exit code 0, got {result.exit_code}. Output: {result.output}"
    mock_s3_sync.assert_called_once_with()
    mock_boto_client.assert_called_once_with("cloudfront", region_name="us-east-1")
    mock_cf_client.list_distributions.assert_called_once()

    expected_invalidation_request = {
        "DistributionId": mock_dist_id,
        "InvalidationBatch": {
            "Paths": {"Quantity": 1, "Items": ["/*"]},
            "CallerReference": mock_caller_ref,
        },
    }
    mock_cf_client.create_invalidation.assert_called_once_with(**expected_invalidation_request)

    assert mock_cf_client.get_invalidation.call_count == 2
    mock_cf_client.get_invalidation.assert_has_calls(
        [
            call(DistributionId=mock_dist_id, Id=mock_invalidation_id),
            call(DistributionId=mock_dist_id, Id=mock_invalidation_id),
        ]
    )
    mock_sleep.assert_called_once_with(10)

    mock_error.assert_not_called()
    mock_success.assert_called_once_with("CloudFront cache has been invalidated")


@patch(f"{MODULE_PATH}.success")
@patch(f"{MODULE_PATH}.error")
@patch(f"{MODULE_PATH}.console")
@patch(f"{MODULE_PATH}.boto3.client")
@patch(f"{MODULE_PATH}.s3_sync")
def test_publish_docs_get_distribution_error(
    mock_s3_sync: MagicMock,
    mock_boto_client: MagicMock,
    mock_console: MagicMock,
    mock_error: MagicMock,
    mock_success: MagicMock,
    runner: CliRunner,
):
    """`list_distributions` failure aborts the publish."""
    mock_cf_client = MagicMock()
    mock_boto_client.return_value = mock_cf_client

    list_dist_response = {
        "ResponseMetadata": {"HTTPStatusCode": 500},
        "Error": {"Message": "Server Error"},
    }
    mock_cf_client.list_distributions.return_value = list_dist_response

    result = runner.invoke(publish_docs_cli)

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)

    mock_s3_sync.assert_called_once_with()
    mock_boto_client.assert_called_once_with("cloudfront", region_name="us-east-1")
    mock_cf_client.list_distributions.assert_called_once()
    mock_error.assert_called_once()
    assert "Failed to get the distribution ID" in mock_error.call_args[0][0]
    assert str(list_dist_response) in mock_error.call_args[0][0]
    mock_success.assert_not_called()


@patch(f"{MODULE_PATH}.success")
@patch(f"{MODULE_PATH}.error")
@patch(f"{MODULE_PATH}.console")
@patch(f"{MODULE_PATH}.boto3.client")
@patch(f"{MODULE_PATH}.s3_sync")
def test_publish_docs_create_invalidation_error(
    mock_s3_sync: MagicMock,
    mock_boto_client: MagicMock,
    mock_console: MagicMock,
    mock_error: MagicMock,
    mock_success: MagicMock,
    runner: CliRunner,
):
    """`create_invalidation` failure aborts the publish."""
    mock_dist_id = "EXAMPLE54321"

    mock_cf_client = MagicMock()
    mock_boto_client.return_value = mock_cf_client
    mock_cf_client.list_distributions.return_value = {
        "ResponseMetadata": {"HTTPStatusCode": 200},
        "DistributionList": {"Items": [{"Id": mock_dist_id}]},
    }

    create_invalid_response = {
        "ResponseMetadata": {"HTTPStatusCode": 400},
        "Error": {"Message": "Bad Request"},
    }
    mock_cf_client.create_invalidation.return_value = create_invalid_response

    result = runner.invoke(publish_docs_cli)

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)

    mock_s3_sync.assert_called_once_with()
    mock_boto_client.assert_called_once_with("cloudfront", region_name="us-east-1")
    mock_cf_client.list_distributions.assert_called_once()
    mock_cf_client.create_invalidation.assert_called_once()
    mock_error.assert_called_once()
    assert "Failed to invalidate the CloudFront cache" in mock_error.call_args[0][0]
    assert str(create_invalid_response) in mock_error.call_args[0][0]
    mock_success.assert_not_called()


# --- Tests for get_distribution_id internal logic ---


@pytest.mark.parametrize(
    "mock_response",
    [
        pytest.param(
            {
                "ResponseMetadata": {"HTTPStatusCode": 200},
                "DistributionList": {"Items": None},
            },
            id="items_is_none",
        ),
        pytest.param(
            {
                "ResponseMetadata": {"HTTPStatusCode": 200},
                "DistributionList": {"Items": []},
            },
            id="items_is_empty_list",
        ),
    ],
)
@patch(f"{MODULE_PATH}.error")
@patch(f"{MODULE_PATH}.console")
def test_get_distribution_id_no_items(mock_console: MagicMock, mock_error: MagicMock, mock_response: dict):
    """`get_distribution_id` aborts when the distribution list is empty."""
    mock_client = MagicMock()
    mock_client.list_distributions.return_value = mock_response

    with pytest.raises(Abort):
        get_distribution_id(mock_client)

    mock_error.assert_called_once()
    assert "No distributions found" in mock_error.call_args[0][0]
    mock_console().print.assert_any_call(mock_response)


@patch(f"{MODULE_PATH}.error")
@patch(f"{MODULE_PATH}.console")
def test_get_distribution_id_api_error(mock_console: MagicMock, mock_error: MagicMock):
    """`get_distribution_id` aborts on a non-200 API response."""
    mock_client = MagicMock()
    api_error_response = {
        "ResponseMetadata": {"HTTPStatusCode": 503},
        "Error": {"Message": "Service Unavailable"},
    }
    mock_client.list_distributions.return_value = api_error_response

    with pytest.raises(Abort):
        get_distribution_id(mock_client)

    mock_error.assert_called_once()
    assert "Failed to get the distribution ID" in mock_error.call_args[0][0]
    assert str(api_error_response) in mock_error.call_args[0][0]


# --- Tests for s3_sync ---


def build_popen_mock(returncode: int = 0, output_lines: list[str] | None = None) -> MagicMock:
    """Build a Popen mock whose context-manager protocol returns a fake process."""
    process = MagicMock()
    process.stdout = output_lines or []
    process.wait.return_value = returncode
    popen = MagicMock()
    popen.__enter__.return_value = process
    popen.__exit__.return_value = None
    return popen


@patch(f"{MODULE_PATH}.Path")
@patch(f"{MODULE_PATH}.subprocess.Popen")
def test_s3_sync_runs_two_sync_passes_with_cache_control(
    mock_popen: MagicMock,
    mock_path_cls: MagicMock,
):
    """
    `s3_sync` touches local files, then runs two scoped `aws s3 sync` passes:
    (1) HTML files only with the short HTML cache-control, (2) everything
    except HTML with the longer asset cache-control. Both passes use `--delete`
    so files removed from the build are cleaned up in their respective scopes.
    Both passes are `sync` rather than `cp --metadata-directive REPLACE` so
    that Content-Type stays inferred from the file extension (REPLACE would
    wipe it back to binary/octet-stream and break CSS/JS loading).
    """
    mock_path_cls.return_value.rglob.return_value = []
    mock_popen.return_value = build_popen_mock()

    s3_sync()

    assert mock_popen.call_count == 2
    html_args = mock_popen.call_args_list[0][0][0]
    asset_args = mock_popen.call_args_list[1][0][0]

    assert html_args[:3] == ["aws", "s3", "sync"]
    assert "--delete" in html_args
    assert "--size-only" not in html_args
    assert html_args[html_args.index("--cache-control") + 1] == HTML_CACHE_CONTROL
    assert html_args[html_args.index("--exclude") + 1] == "*"
    assert html_args[html_args.index("--include") + 1] == "*.html"

    assert asset_args[:3] == ["aws", "s3", "sync"]
    assert "--delete" in asset_args
    assert "--size-only" not in asset_args
    assert asset_args[asset_args.index("--cache-control") + 1] == ASSET_CACHE_CONTROL
    assert asset_args[asset_args.index("--exclude") + 1] == "*.html"

    # `--metadata-directive REPLACE` was the exact regression: it wiped
    # Content-Type back to binary/octet-stream. Assert it never appears in
    # any of the publish-docs commands, not just the asset pass.
    for popen_call in mock_popen.call_args_list:
        argv = popen_call[0][0]
        assert "--metadata-directive" not in argv, f"--metadata-directive must not appear in any aws call (got: {argv})"


@patch(f"{MODULE_PATH}.Path")
@patch(f"{MODULE_PATH}.subprocess.Popen")
def test_s3_sync_touches_local_files(
    mock_popen: MagicMock,
    mock_path_cls: MagicMock,
):
    """Every file under `site/` gets touched before sync so its mtime is fresh."""
    file_a = MagicMock()
    file_a.is_file.return_value = True
    file_b = MagicMock()
    file_b.is_file.return_value = True
    directory = MagicMock()
    directory.is_file.return_value = False

    mock_path_cls.return_value.rglob.return_value = [file_a, directory, file_b]
    mock_popen.return_value = build_popen_mock()

    s3_sync()

    mock_path_cls.assert_called_with("site")
    file_a.touch.assert_called_once_with()
    file_b.touch.assert_called_once_with()
    directory.touch.assert_not_called()


@patch(f"{MODULE_PATH}.console")
@patch(f"{MODULE_PATH}.Path")
@patch(f"{MODULE_PATH}.subprocess.Popen")
def test_s3_sync_streams_command_output(
    mock_popen: MagicMock,
    mock_path_cls: MagicMock,
    mock_console: MagicMock,
):
    """Lines emitted by the aws-cli subprocess are printed as they arrive."""
    mock_path_cls.return_value.rglob.return_value = []
    mock_popen.side_effect = [
        build_popen_mock(output_lines=["upload: site/index.html\n", "upload: site/about.html\n"]),
        build_popen_mock(),
    ]

    s3_sync()

    printed = [call_args.args[0] for call_args in mock_console().print.call_args_list]
    assert "upload: site/index.html" in printed
    assert "upload: site/about.html" in printed


@patch(f"{MODULE_PATH}.Path")
@patch(f"{MODULE_PATH}.subprocess.Popen")
def test_s3_sync_raises_when_aws_fails(
    mock_popen: MagicMock,
    mock_path_cls: MagicMock,
):
    """A non-zero aws-cli exit aborts the publish, with the exit code in the message."""
    mock_path_cls.return_value.rglob.return_value = []
    mock_popen.return_value = build_popen_mock(returncode=1)

    with pytest.raises(RuntimeError) as excinfo:
        s3_sync()

    assert "exited with status 1" in str(excinfo.value)
    mock_popen.assert_called_once()
