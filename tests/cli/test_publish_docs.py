# tests/cli/commands/test_publish_docs.py
import os
from unittest.mock import MagicMock, call, patch

import pytest
from click.exceptions import Abort
from click.testing import CliRunner

from mitup_bot.cli.commands.publish_docs import cli as publish_docs_cli
from mitup_bot.cli.commands.publish_docs import get_distribution_id, s3_sync

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


# Patch objects where they are looked up (in the module under test)
@patch(f"{MODULE_PATH}.success")
@patch(f"{MODULE_PATH}.error")
@patch(f"{MODULE_PATH}.console")
@patch(f"{MODULE_PATH}.boto3.client")
@patch(f"{MODULE_PATH}.s3_sync")
def test_publish_docs_no_updates(
    mock_s3_sync: MagicMock,
    mock_boto_client: MagicMock,
    mock_console: MagicMock,
    mock_error: MagicMock,
    mock_success: MagicMock,
    runner: CliRunner,
):
    """Test the command exits early if s3_sync returns no files."""
    mock_s3_sync.return_value = []

    result = runner.invoke(publish_docs_cli)

    assert result.exit_code == 0, f"Expected exit code 0, but got {result.exit_code}. Output: {result.output}"
    mock_s3_sync.assert_called_once()
    mock_console().print.assert_any_call("No files have been updated. No need to invalidate the cache.")
    mock_boto_client.assert_not_called()
    mock_error.assert_not_called()
    # success() is not called because the function returns early.
    mock_success.assert_not_called()


@patch(f"{MODULE_PATH}.sleep", return_value=None)  # Mock sleep to speed up test
@patch(f"{MODULE_PATH}.success")
@patch(f"{MODULE_PATH}.error")
@patch(f"{MODULE_PATH}.console")
@patch(f"{MODULE_PATH}.boto3.client")
@patch(f"{MODULE_PATH}.s3_sync")
def test_publish_docs_success(
    mock_s3_sync: MagicMock,
    mock_boto_client: MagicMock,
    mock_console: MagicMock,
    mock_error: MagicMock,
    mock_success: MagicMock,
    mock_sleep: MagicMock,
    runner: CliRunner,
):
    """Test the happy path where files are updated and invalidation succeeds."""
    mock_updated_files = ["/index.html", "/styles/main.css"]
    mock_dist_id = "EXAMPLE12345"
    mock_invalidation_id = "INVALIDATION_ID_67890"
    # Use the mocked env var directly
    mock_caller_ref = f"mitup-ci-{os.environ['CI_COMMIT_SHORT_SHA']}"

    mock_s3_sync.return_value = mock_updated_files

    # Mock CloudFront client and its methods
    mock_cf_client = MagicMock()
    mock_boto_client.return_value = mock_cf_client

    # Mock list_distributions response
    mock_cf_client.list_distributions.return_value = {
        "ResponseMetadata": {"HTTPStatusCode": 200},
        "DistributionList": {"Items": [{"Id": mock_dist_id}]},
    }

    # Mock create_invalidation response
    mock_cf_client.create_invalidation.return_value = {
        "ResponseMetadata": {"HTTPStatusCode": 201},
        "Invalidation": {"Id": mock_invalidation_id},
    }

    # Mock get_invalidation response (first incomplete, then complete)
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

    assert result.exit_code == 0, f"Expected exit code 0, but got {result.exit_code}. Output: {result.output}"
    mock_s3_sync.assert_called_once()
    mock_boto_client.assert_called_once_with("cloudfront", region_name="us-east-1")

    # Verify get_distribution_id called implicitly via list_distributions
    mock_cf_client.list_distributions.assert_called_once()
    mock_console().print.assert_any_call("[bold]Distribution ID[/bold]:", mock_dist_id)

    # Verify create_invalidation called with correct parameters
    expected_invalidation_request = {
        "DistributionId": mock_dist_id,
        "InvalidationBatch": {
            "Paths": {"Quantity": len(mock_updated_files), "Items": mock_updated_files},
            "CallerReference": mock_caller_ref,
        },
    }
    mock_cf_client.create_invalidation.assert_called_once_with(**expected_invalidation_request)
    mock_console().print.assert_any_call("[bold]Invalidation request[/bold]:")
    # Verify the request dictionary was printed
    mock_console().print.assert_any_call(expected_invalidation_request)

    # Verify get_invalidation polling logic
    assert mock_cf_client.get_invalidation.call_count == 2
    expected_get_calls = [
        call(DistributionId=mock_dist_id, Id=mock_invalidation_id),
        call(DistributionId=mock_dist_id, Id=mock_invalidation_id),
    ]
    mock_cf_client.get_invalidation.assert_has_calls(expected_get_calls)
    mock_sleep.assert_called_once_with(10)  # Check that sleep was called during polling

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
    """Test error handling when list_distributions fails."""
    mock_s3_sync.return_value = ["/some/file.html"]

    mock_cf_client = MagicMock()
    mock_boto_client.return_value = mock_cf_client

    # Simulate list_distributions failure (e.g., bad status code)
    list_dist_response = {
        "ResponseMetadata": {"HTTPStatusCode": 500},
        "Error": {"Message": "Server Error"},
    }
    mock_cf_client.list_distributions.return_value = list_dist_response

    result = runner.invoke(publish_docs_cli)

    assert result.exit_code != 0, "Expected non-zero exit code due to click.Abort"
    # Check that SystemExit was raised (which click.Abort does)
    assert isinstance(result.exception, SystemExit)

    mock_s3_sync.assert_called_once()
    mock_boto_client.assert_called_once_with("cloudfront", region_name="us-east-1")
    mock_cf_client.list_distributions.assert_called_once()
    mock_error.assert_called_once()
    # Check that the error message contains the expected text and the failing response
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
    """Test error handling when create_invalidation fails."""
    mock_s3_sync.return_value = ["/another/file.js"]
    mock_dist_id = "EXAMPLE54321"

    mock_cf_client = MagicMock()
    mock_boto_client.return_value = mock_cf_client

    mock_cf_client.list_distributions.return_value = {
        "ResponseMetadata": {"HTTPStatusCode": 200},
        "DistributionList": {"Items": [{"Id": mock_dist_id}]},
    }

    # Simulate create_invalidation failure
    create_invalid_response = {
        "ResponseMetadata": {"HTTPStatusCode": 400},
        "Error": {"Message": "Bad Request"},
    }
    mock_cf_client.create_invalidation.return_value = create_invalid_response

    result = runner.invoke(publish_docs_cli)

    assert result.exit_code != 0, "Expected non-zero exit code due to click.Abort"
    assert isinstance(result.exception, SystemExit)

    mock_s3_sync.assert_called_once()
    mock_boto_client.assert_called_once_with("cloudfront", region_name="us-east-1")
    mock_cf_client.list_distributions.assert_called_once()
    mock_cf_client.create_invalidation.assert_called_once()  # It was called
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
                "DistributionList": {"Items": None},  # Test None case
            },
            id="items_is_none",
        ),
        pytest.param(
            {
                "ResponseMetadata": {"HTTPStatusCode": 200},
                "DistributionList": {"Items": []},  # Test empty list case
            },
            id="items_is_empty_list",
        ),
    ],
)
@patch(f"{MODULE_PATH}.error")
@patch(f"{MODULE_PATH}.console")
def test_get_distribution_id_no_items(mock_console: MagicMock, mock_error: MagicMock, mock_response: dict):
    """Test get_distribution_id when no distribution items are returned."""
    mock_client = MagicMock()
    mock_client.list_distributions.return_value = mock_response

    with pytest.raises(Abort):
        get_distribution_id(mock_client)

    mock_error.assert_called_once()
    assert "No distributions found" in mock_error.call_args[0][0]
    # Check that the raw response was printed for debugging
    mock_console().print.assert_any_call(mock_response)


@patch(f"{MODULE_PATH}.error")
@patch(f"{MODULE_PATH}.console")
def test_get_distribution_id_api_error(mock_console: MagicMock, mock_error: MagicMock):
    """Test get_distribution_id when the API call itself fails (bad status code)."""
    mock_client = MagicMock()
    api_error_response = {
        "ResponseMetadata": {"HTTPStatusCode": 503},  # Simulate API error status
        "Error": {"Message": "Service Unavailable"},
    }
    mock_client.list_distributions.return_value = api_error_response

    with pytest.raises(Abort):
        get_distribution_id(mock_client)

    mock_error.assert_called_once()
    assert "Failed to get the distribution ID" in mock_error.call_args[0][0]
    assert str(api_error_response) in mock_error.call_args[0][0]


# --- Tests for s3_sync internal logic ---


@pytest.mark.parametrize(
    "mock_stdout_str, expected_files",
    [
        pytest.param(
            (
                "upload: site/index.html to s3://test.domain.com/index.html\n"
                "upload: site/assets/style.css to s3://test.domain.com/assets/style.css\n"
            ),
            ["/index.html", "/assets/style.css"],
            id="uploads_only",
        ),
        pytest.param(
            ("delete: s3://test.domain.com/old_page.html\ndelete: s3://test.domain.com/images/unused.jpg\n"),
            ["/old_page.html", "/images/unused.jpg"],
            id="deletes_only",
        ),
        pytest.param(
            (
                "upload: site/new_feature/script.js to s3://test.domain.com/new_feature/script.js\n"
                "delete: s3://test.domain.com/deprecated/style.css\n"
                "upload: site/about.html to s3://test.domain.com/about.html\n"
            ),
            ["/new_feature/script.js", "/deprecated/style.css", "/about.html"],
            id="mixed_updates",
        ),
        pytest.param(
            "",  # No output
            [],
            id="no_changes",
        ),
        pytest.param(
            (
                "Completed 1 part(s) 15.1 KiB / 15.1 KiB...\n"  # Unexpected line
                "upload: site/file.txt to s3://test.domain.com/file.txt\n"
                "Some other random output\n"
            ),
            ["/file.txt"],
            id="unexpected_output_lines",
        ),
    ],
)
@patch(f"{MODULE_PATH}.subprocess.Popen")
def test_s3_sync_parsing(mock_popen: MagicMock, mock_stdout_str: str, expected_files: list[str]):
    """Test s3_sync correctly parses various aws s3 sync stdout lines."""
    mock_process = MagicMock()
    mock_stdout_bytes = mock_stdout_str.encode("utf-8")
    mock_process.communicate.return_value = (mock_stdout_bytes, b"")
    mock_process.returncode = 0
    mock_popen.return_value = mock_process

    actual_files = s3_sync()

    # Sort for comparison as order might not be guaranteed and doesn't matter for invalidation
    assert sorted(actual_files) == sorted(expected_files)
    mock_popen.assert_called_once()
    # Optionally check the command structure if needed, e.g. for the first call
    if mock_popen.call_count == 1:
        # Check the first few elements of the command list
        cmd_list = mock_popen.call_args[0][0]
        assert cmd_list[:3] == ["aws", "s3", "sync"]


@patch(f"{MODULE_PATH}.subprocess.Popen")
def test_s3_sync_error(mock_popen: MagicMock):
    """Test s3_sync raises an error if the aws command fails."""
    mock_process = MagicMock()
    mock_stderr = b"An error occurred"
    mock_process.communicate.return_value = (b"", mock_stderr)
    mock_process.returncode = 1  # Non-zero return code indicates failure
    mock_popen.return_value = mock_process

    with pytest.raises(RuntimeError) as excinfo:
        s3_sync()

    assert "Failed to sync files to S3" in str(excinfo.value)
    assert mock_stderr.decode() in str(excinfo.value)
    mock_popen.assert_called_once()


@patch(f"{MODULE_PATH}.console")
@patch(f"{MODULE_PATH}.subprocess.Popen")
def test_s3_sync_unparseable_path(mock_popen: MagicMock, mock_console: MagicMock):
    """Test s3_sync handles URIs where the path cannot be parsed."""
    mock_process = MagicMock()
    # Simulate output with a URI that urlparse might return an empty path for
    mock_stdout_str = "upload: site/file to s3://test.domain.com\n"
    mock_stdout_bytes = mock_stdout_str.encode("utf-8")
    mock_process.communicate.return_value = (mock_stdout_bytes, b"")
    mock_process.returncode = 0
    mock_popen.return_value = mock_process

    actual_files = s3_sync()

    # Expect no files to be returned as the path couldn't be parsed
    assert actual_files == []
    mock_popen.assert_called_once()
    # Assert that the warning message was printed
    mock_console().print.assert_any_call("[yellow]Could not parse path from S3 URI:[/yellow] s3://test.domain.com")


@patch(f"{MODULE_PATH}.sleep", return_value=None)
@patch(f"{MODULE_PATH}.success")
@patch(f"{MODULE_PATH}.error")
@patch(f"{MODULE_PATH}.console")
@patch(f"{MODULE_PATH}.boto3.client")
@patch(f"{MODULE_PATH}.s3_sync")
def test_publish_docs_invalidate_all_success(
    mock_s3_sync: MagicMock,
    mock_boto_client: MagicMock,
    mock_console: MagicMock,
    mock_error: MagicMock,
    mock_success: MagicMock,
    mock_sleep: MagicMock,
    runner: CliRunner,
):
    """Test the happy path using --invalidate-all flag."""
    # s3_sync might return files or not, shouldn't matter for --invalidate-all
    mock_s3_sync.return_value = ["/some/updated/file.html"]
    mock_dist_id = "EXAMPLE_ALL_123"
    mock_invalidation_id = "INVALIDATION_ALL_456"
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

    result = runner.invoke(publish_docs_cli, ["--invalidate-all"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    mock_s3_sync.assert_called_once()  # s3_sync should still be called
    mock_boto_client.assert_called_once_with("cloudfront", region_name="us-east-1")
    mock_cf_client.list_distributions.assert_called_once()

    expected_invalidation_request = {
        "DistributionId": mock_dist_id,
        "InvalidationBatch": {
            "Paths": {"Quantity": 1, "Items": ["/*"]},  # Check for wildcard invalidation
            "CallerReference": mock_caller_ref,
        },
    }
    mock_cf_client.create_invalidation.assert_called_once_with(**expected_invalidation_request)
    mock_console().print.assert_any_call("[bold yellow]Invalidating entire cache (/*)[/bold yellow]")
    assert mock_cf_client.get_invalidation.call_count == 2
    mock_error.assert_not_called()
    mock_success.assert_called_once_with("CloudFront cache has been invalidated")


@patch(f"{MODULE_PATH}.sleep", return_value=None)
@patch(f"{MODULE_PATH}.success")
@patch(f"{MODULE_PATH}.error")
@patch(f"{MODULE_PATH}.console")
@patch(f"{MODULE_PATH}.boto3.client")
@patch(f"{MODULE_PATH}.s3_sync")
def test_publish_docs_invalidate_all_no_updates(
    mock_s3_sync: MagicMock,
    mock_boto_client: MagicMock,
    mock_console: MagicMock,
    mock_error: MagicMock,
    mock_success: MagicMock,
    mock_sleep: MagicMock,
    runner: CliRunner,
):
    """Test --invalidate-all works even if s3_sync returns no updates."""
    mock_s3_sync.return_value = []  # Simulate no file updates
    mock_dist_id = "EXAMPLE_ALL_789"
    mock_invalidation_id = "INVALIDATION_ALL_012"
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

    result = runner.invoke(publish_docs_cli, ["--invalidate-all"])

    assert result.exit_code == 0, f"CLI failed: {result.output}"
    mock_s3_sync.assert_called_once()
    # Should NOT have printed the "No files updated" message
    assert mock_console().print.call_args_list != [call("No files have been updated. No need to invalidate the cache.")]
    mock_boto_client.assert_called_once_with("cloudfront", region_name="us-east-1")
    mock_cf_client.list_distributions.assert_called_once()

    expected_invalidation_request = {
        "DistributionId": mock_dist_id,
        "InvalidationBatch": {
            "Paths": {"Quantity": 1, "Items": ["/*"]},  # Still check for wildcard
            "CallerReference": mock_caller_ref,
        },
    }
    mock_cf_client.create_invalidation.assert_called_once_with(**expected_invalidation_request)
    mock_console().print.assert_any_call("[bold yellow]Invalidating entire cache (/*)[/bold yellow]")
    assert mock_cf_client.get_invalidation.call_count == 2
    mock_error.assert_not_called()
    mock_success.assert_called_once_with("CloudFront cache has been invalidated")
