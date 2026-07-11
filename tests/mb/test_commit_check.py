from pathlib import Path

import pytest

from mb import commit_check

CONFIG = """
additional_commit_types:
  Feat:
    description: New feature.
    emoji: "✨"
  Fix:
    description: Bug fix.
    emoji: "🐛"
"""


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "commits_check_config.yaml"
    path.write_text(CONFIG)
    return path


@pytest.fixture
def formatter(config_path: Path) -> commit_check.CommitMessageFormatter:
    return commit_check.CommitMessageFormatter(config_path)


def test_replaces_type_with_emoji_and_capitalizes(formatter: commit_check.CommitMessageFormatter):
    error, formatted = formatter.format_commit_message("feat: add user authentication")

    assert error is None
    assert formatted == "✨ Add user authentication"


def test_preserves_scope_and_body(formatter: commit_check.CommitMessageFormatter):
    error, formatted = formatter.format_commit_message("fix(api): correct validation\n\nLonger body here.")

    assert error is None
    assert formatted == "🐛(api) Correct validation\n\nLonger body here."


def test_type_matching_is_case_insensitive(formatter: commit_check.CommitMessageFormatter):
    error, formatted = formatter.format_commit_message("FEAT: shout the feature")

    assert error is None
    assert formatted == "✨ Shout the feature"


def test_merge_commits_pass_through_untouched(formatter: commit_check.CommitMessageFormatter):
    error, formatted = formatter.format_commit_message("Merge branch 'main' into feature")

    assert error is None
    assert formatted == "Merge branch 'main' into feature"


def test_already_emojified_message_is_accepted_unchanged(formatter: commit_check.CommitMessageFormatter):
    error, formatted = formatter.format_commit_message("✨ Add user authentication")

    assert error is None
    assert formatted == "✨ Add user authentication"


def test_already_emojified_message_with_scope_and_body_is_accepted_unchanged(
    formatter: commit_check.CommitMessageFormatter,
):
    error, formatted = formatter.format_commit_message("🐛(api) Correct validation\n\nLonger body here.")

    assert error is None
    assert formatted == "🐛(api) Correct validation\n\nLonger body here."


def test_emojified_message_with_lowercase_description_is_normalized(formatter: commit_check.CommitMessageFormatter):
    error, formatted = formatter.format_commit_message("🐛(api) correct validation")

    assert error is None
    assert formatted == "🐛(api) Correct validation"


def test_emojified_message_without_description_is_rejected(formatter: commit_check.CommitMessageFormatter):
    error, formatted = formatter.format_commit_message("✨ ")

    assert formatted is None
    assert error is not None
    assert "Invalid commit message format" in error


def test_formatting_is_idempotent(formatter: commit_check.CommitMessageFormatter):
    _, once = formatter.format_commit_message("fix(api): correct validation")
    assert once is not None
    _, twice = formatter.format_commit_message(once)

    assert twice == once


def test_missing_colon_is_rejected(formatter: commit_check.CommitMessageFormatter):
    error, formatted = formatter.format_commit_message("feat add user authentication")

    assert formatted is None
    assert error is not None
    assert "Invalid commit message format" in error


def test_unknown_type_is_rejected(formatter: commit_check.CommitMessageFormatter):
    error, formatted = formatter.format_commit_message("wip: not a real type")

    assert formatted is None
    assert error is not None
    assert "Invalid commit type: 'wip'" in error


def test_blank_description_is_rejected(formatter: commit_check.CommitMessageFormatter):
    error, formatted = formatter.format_commit_message("feat:   ")

    assert formatted is None
    assert error is not None
    assert "Invalid commit message format" in error


def test_check_commit_file_rewrites_the_message_in_place(config_path: Path, tmp_path: Path):
    commit_msg_file = tmp_path / "COMMIT_EDITMSG"
    commit_msg_file.write_text("feat(ci): flip the pipeline to uv")

    exit_code = commit_check.check_commit_file(commit_msg_file, config_path)

    assert exit_code == 0
    assert commit_msg_file.read_text() == "✨(ci) Flip the pipeline to uv"


def test_check_commit_file_leaves_valid_emoji_message_byte_unchanged(config_path: Path, tmp_path: Path):
    commit_msg_file = tmp_path / "COMMIT_EDITMSG"
    # git writes the message with a trailing newline; a re-run must not touch a single byte.
    original = "✨(ci) Flip the pipeline to uv\n"
    commit_msg_file.write_text(original)
    before = commit_msg_file.read_bytes()

    exit_code = commit_check.check_commit_file(commit_msg_file, config_path)

    assert exit_code == 0
    assert commit_msg_file.read_bytes() == before


def test_check_commit_file_is_idempotent_across_reruns(config_path: Path, tmp_path: Path):
    commit_msg_file = tmp_path / "COMMIT_EDITMSG"
    commit_msg_file.write_text("feat(ci): flip the pipeline to uv\n")

    assert commit_check.check_commit_file(commit_msg_file, config_path) == 0
    after_first = commit_msg_file.read_bytes()
    # The second run mirrors `git commit --amend --no-edit`: same emoji message, must pass untouched.
    assert commit_check.check_commit_file(commit_msg_file, config_path) == 0
    assert commit_msg_file.read_bytes() == after_first


def test_check_commit_file_fails_on_invalid_message(config_path: Path, tmp_path: Path):
    commit_msg_file = tmp_path / "COMMIT_EDITMSG"
    commit_msg_file.write_text("no conventional prefix here")

    exit_code = commit_check.check_commit_file(commit_msg_file, config_path)

    assert exit_code == 1
    assert commit_msg_file.read_text() == "no conventional prefix here"


def test_check_commit_file_fails_when_config_is_missing(tmp_path: Path):
    commit_msg_file = tmp_path / "COMMIT_EDITMSG"
    commit_msg_file.write_text("feat: anything")

    exit_code = commit_check.check_commit_file(commit_msg_file, tmp_path / "missing.yaml")

    assert exit_code == 1
