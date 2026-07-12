import os
from unittest.mock import MagicMock, call, patch

import pytest
import typer

from mb import console, docs_ops

MODULE_PATH = "mb.docs_ops"


@pytest.fixture(autouse=True)
def plain_console(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "200")
    console.configure(plain=True)


@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BOT_DOMAIN", "test.domain.com")
    monkeypatch.setenv("CI_COMMIT_SHORT_SHA", "testsha123")


def combined(capsys: pytest.CaptureFixture[str]) -> str:
    captured = capsys.readouterr()
    return captured.out + captured.err


@patch(f"{MODULE_PATH}.sleep", return_value=None)
@patch(f"{MODULE_PATH}.boto3.client")
@patch(f"{MODULE_PATH}.s3_sync")
def test_publish_docs_invalidates_everything(
    mock_s3_sync: MagicMock,
    mock_boto_client: MagicMock,
    mock_sleep: MagicMock,
    capsys: pytest.CaptureFixture[str],
):
    """Every publish-docs run invalidates `/*` once the sync has completed."""
    mock_dist_id = "EXAMPLE12345"
    mock_invalidation_id = "INVALIDATION_ID_67890"
    mock_caller_ref = f"mitup-ci-{os.environ['CI_COMMIT_SHORT_SHA']}"

    mock_cf_client = MagicMock()
    mock_boto_client.return_value = mock_cf_client
    mock_cf_client.list_distributions.return_value = {
        "ResponseMetadata": {"HTTPStatusCode": 200},
        "DistributionList": {"Items": [{"Id": mock_dist_id, "Aliases": {"Items": [os.environ["BOT_DOMAIN"]]}}]},
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

    docs_ops.publish_docs()

    mock_s3_sync.assert_called_once_with()
    mock_boto_client.assert_called_once_with("cloudfront", region_name="us-east-1")
    mock_cf_client.list_distributions.assert_called_once()

    expected_invalidation_request = {
        "DistributionId": mock_dist_id,
        "InvalidationBatch": {"Paths": {"Quantity": 1, "Items": ["/*"]}, "CallerReference": mock_caller_ref},
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
    assert "CloudFront cache has been invalidated" in combined(capsys)


@patch(f"{MODULE_PATH}.boto3.client")
@patch(f"{MODULE_PATH}.s3_sync")
def test_publish_docs_get_distribution_error(
    mock_s3_sync: MagicMock, mock_boto_client: MagicMock, capsys: pytest.CaptureFixture[str]
):
    """`list_distributions` failure aborts the publish."""
    mock_cf_client = MagicMock()
    mock_boto_client.return_value = mock_cf_client

    list_dist_response = {"ResponseMetadata": {"HTTPStatusCode": 500}, "Error": {"Message": "Server Error"}}
    mock_cf_client.list_distributions.return_value = list_dist_response

    with pytest.raises(typer.Abort):
        docs_ops.publish_docs()

    mock_s3_sync.assert_called_once_with()
    mock_boto_client.assert_called_once_with("cloudfront", region_name="us-east-1")
    mock_cf_client.list_distributions.assert_called_once()
    assert "Failed to get the distribution ID" in combined(capsys)


@patch(f"{MODULE_PATH}.boto3.client")
@patch(f"{MODULE_PATH}.s3_sync")
def test_publish_docs_create_invalidation_error(
    mock_s3_sync: MagicMock, mock_boto_client: MagicMock, capsys: pytest.CaptureFixture[str]
):
    """`create_invalidation` failure aborts the publish."""
    mock_dist_id = "EXAMPLE54321"

    mock_cf_client = MagicMock()
    mock_boto_client.return_value = mock_cf_client
    mock_cf_client.list_distributions.return_value = {
        "ResponseMetadata": {"HTTPStatusCode": 200},
        "DistributionList": {"Items": [{"Id": mock_dist_id, "Aliases": {"Items": [os.environ["BOT_DOMAIN"]]}}]},
    }
    create_invalid_response = {"ResponseMetadata": {"HTTPStatusCode": 400}, "Error": {"Message": "Bad Request"}}
    mock_cf_client.create_invalidation.return_value = create_invalid_response

    with pytest.raises(typer.Abort):
        docs_ops.publish_docs()

    mock_cf_client.create_invalidation.assert_called_once()
    assert "Failed to invalidate the CloudFront cache" in combined(capsys)


@pytest.mark.parametrize(
    "distribution_response",
    [
        {"ResponseMetadata": {"HTTPStatusCode": 200}, "DistributionList": {"Items": None}},
        {"ResponseMetadata": {"HTTPStatusCode": 200}, "DistributionList": {"Items": []}},
    ],
    ids=["items_is_none", "items_is_empty_list"],
)
def test_get_distribution_id_no_items(distribution_response: dict, capsys: pytest.CaptureFixture[str]):
    """`get_distribution_id` aborts when the distribution list is empty."""
    mock_client = MagicMock()
    mock_client.list_distributions.return_value = distribution_response

    with pytest.raises(typer.Abort):
        docs_ops.get_distribution_id(mock_client)

    assert "No distributions found" in combined(capsys)


def test_get_distribution_id_api_error(capsys: pytest.CaptureFixture[str]):
    """`get_distribution_id` aborts on a non-200 API response."""
    mock_client = MagicMock()
    api_error_response = {"ResponseMetadata": {"HTTPStatusCode": 503}, "Error": {"Message": "Service Unavailable"}}
    mock_client.list_distributions.return_value = api_error_response

    with pytest.raises(typer.Abort):
        docs_ops.get_distribution_id(mock_client)

    assert "Failed to get the distribution ID" in combined(capsys)


def test_get_distribution_id_single_match():
    """The sole distribution is selected when its aliases include `BOT_DOMAIN`."""
    mock_client = MagicMock()
    mock_client.list_distributions.return_value = {
        "ResponseMetadata": {"HTTPStatusCode": 200},
        "DistributionList": {"Items": [{"Id": "MATCHING123", "Aliases": {"Items": [os.environ["BOT_DOMAIN"]]}}]},
    }

    assert docs_ops.get_distribution_id(mock_client) == "MATCHING123"


def test_get_distribution_id_selects_alias_match_among_many():
    """The distribution whose aliases include `BOT_DOMAIN` is picked, not the first one."""
    mock_client = MagicMock()
    mock_client.list_distributions.return_value = {
        "ResponseMetadata": {"HTTPStatusCode": 200},
        "DistributionList": {
            "Items": [
                {"Id": "OTHER111", "Aliases": {"Items": ["other.domain.com"]}},
                {"Id": "MATCHING222", "Aliases": {"Items": [os.environ["BOT_DOMAIN"]]}},
                {"Id": "NOALIASES333", "Aliases": {"Items": None}},
            ]
        },
    }

    assert docs_ops.get_distribution_id(mock_client) == "MATCHING222"


def test_get_distribution_id_no_alias_match(capsys: pytest.CaptureFixture[str]):
    """`get_distribution_id` aborts when no distribution carries `BOT_DOMAIN` as an alias."""
    mock_client = MagicMock()
    mock_client.list_distributions.return_value = {
        "ResponseMetadata": {"HTTPStatusCode": 200},
        "DistributionList": {"Items": [{"Id": "OTHER111", "Aliases": {"Items": ["other.domain.com"]}}]},
    }

    with pytest.raises(typer.Abort):
        docs_ops.get_distribution_id(mock_client)

    assert f"No CloudFront distribution has {os.environ['BOT_DOMAIN']} as an alias" in combined(capsys)


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
def test_s3_sync_runs_two_sync_passes_with_cache_control(mock_popen: MagicMock, mock_path_cls: MagicMock):
    mock_path_cls.return_value.rglob.return_value = []
    mock_popen.return_value = build_popen_mock()

    docs_ops.s3_sync()

    assert mock_popen.call_count == 2
    html_args = mock_popen.call_args_list[0][0][0]
    asset_args = mock_popen.call_args_list[1][0][0]

    assert html_args[:3] == ["aws", "s3", "sync"]
    assert "--delete" in html_args
    assert html_args[html_args.index("--cache-control") + 1] == docs_ops.HTML_CACHE_CONTROL
    assert html_args[html_args.index("--exclude") + 1] == "*"
    assert html_args[html_args.index("--include") + 1] == "*.html"

    assert asset_args[:3] == ["aws", "s3", "sync"]
    assert "--delete" in asset_args
    assert asset_args[asset_args.index("--cache-control") + 1] == docs_ops.ASSET_CACHE_CONTROL
    assert asset_args[asset_args.index("--exclude") + 1] == "*.html"

    # `--metadata-directive REPLACE` wiped Content-Type back to binary/octet-stream: never allow it.
    for popen_call in mock_popen.call_args_list:
        argv = popen_call[0][0]
        assert "--metadata-directive" not in argv


@patch(f"{MODULE_PATH}.Path")
@patch(f"{MODULE_PATH}.subprocess.Popen")
def test_s3_sync_touches_local_files(mock_popen: MagicMock, mock_path_cls: MagicMock):
    file_a = MagicMock()
    file_a.is_file.return_value = True
    file_b = MagicMock()
    file_b.is_file.return_value = True
    directory = MagicMock()
    directory.is_file.return_value = False

    mock_path_cls.return_value.rglob.return_value = [file_a, directory, file_b]
    mock_popen.return_value = build_popen_mock()

    docs_ops.s3_sync()

    mock_path_cls.assert_called_with("site")
    file_a.touch.assert_called_once_with()
    file_b.touch.assert_called_once_with()
    directory.touch.assert_not_called()


@patch(f"{MODULE_PATH}.Path")
@patch(f"{MODULE_PATH}.subprocess.Popen")
def test_s3_sync_streams_command_output(
    mock_popen: MagicMock, mock_path_cls: MagicMock, capsys: pytest.CaptureFixture[str]
):
    mock_path_cls.return_value.rglob.return_value = []
    mock_popen.side_effect = [
        build_popen_mock(output_lines=["upload: site/index.html\n", "upload: site/about.html\n"]),
        build_popen_mock(),
    ]

    docs_ops.s3_sync()

    output = combined(capsys)
    assert "upload: site/index.html" in output
    assert "upload: site/about.html" in output


@patch(f"{MODULE_PATH}.Path")
@patch(f"{MODULE_PATH}.subprocess.Popen")
def test_s3_sync_raises_when_aws_fails(mock_popen: MagicMock, mock_path_cls: MagicMock):
    mock_path_cls.return_value.rglob.return_value = []
    mock_popen.return_value = build_popen_mock(returncode=1)

    with pytest.raises(RuntimeError) as excinfo:
        docs_ops.s3_sync()

    assert "exited with status 1" in str(excinfo.value)
    mock_popen.assert_called_once()
