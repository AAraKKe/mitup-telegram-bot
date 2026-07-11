import re
from pathlib import Path

import yaml

from . import console

# Regex pattern for conventional commit format (case-insensitive type)
# Format: Type[(scope)]: description
COMMIT_PATTERN = re.compile(
    r"^(?P<type>[A-Za-z]+)"  # Type (any case)
    r"(?:\((?P<scope>[a-z0-9_\-/, ]+)\))?"  # Optional scope
    r": "  # Colon and space
    r"(?P<description>.+)$",  # Description
    re.IGNORECASE,
)


class CommitConfigError(RuntimeError):
    """Raised when commits_check_config.yaml is missing or cannot be parsed."""


def load_allowed_types(config_path: Path) -> dict[str, dict]:
    try:
        with open(config_path) as config_file:
            config = yaml.safe_load(config_file)
            return config.get("additional_commit_types", {})
    except FileNotFoundError as error:
        raise CommitConfigError(f"Configuration file not found: {config_path}") from error
    except yaml.YAMLError as error:
        raise CommitConfigError(f"Error parsing YAML configuration: {error}") from error


class CommitMessageFormatter:
    """Validates a conventional commit message and replaces its type prefix with the mapped emoji."""

    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.allowed_types = load_allowed_types(config_path)
        # Create case-insensitive lookup
        self.type_lookup = {k.lower(): k for k in self.allowed_types}

    def format_commit_message(self, message: str) -> tuple[str | None, str | None]:
        """Return an (error_message, formatted_message) pair; exactly one side is set."""
        # Skip merge commits
        if message.startswith("Merge "):
            return None, message

        # Extract the first line (subject)
        lines = message.strip().split("\n")
        subject = lines[0].strip()

        match = COMMIT_PATTERN.match(subject)
        if not match:
            return self._format_error_message(subject), None

        # Validate type (case-insensitive)
        commit_type = match.group("type")
        if commit_type.lower() not in self.type_lookup:
            return self._format_type_error(commit_type), None

        # Get the canonical type name and its emoji
        canonical_type = self.type_lookup[commit_type.lower()]
        emoji = self.allowed_types[canonical_type].get("emoji", "")

        # Get description and capitalize first letter
        description = match.group("description").strip()
        if not description:
            return "Commit message must have a description after the colon.", None
        if description[0].islower() and description[0].isalpha():
            description = description[0].upper() + description[1:]

        scope = match.group("scope")
        formatted_subject = emoji
        if scope:
            formatted_subject += f"({scope})"
        formatted_subject += f" {description}"

        # Rebuild the full message, preserving body and footer
        formatted_lines = [formatted_subject, *lines[1:]]
        return None, "\n".join(formatted_lines)

    def format_file(self, commit_msg_file: Path) -> int:
        """Rewrite the commit message file in place. Returns the hook exit code."""
        try:
            message = commit_msg_file.read_text()
        except FileNotFoundError:
            console.error(f"Commit message file not found: {commit_msg_file}")
            return 1

        error_message, formatted_message = self.format_commit_message(message)

        if formatted_message is None:
            console.error("Commit message formatting failed!")
            assert error_message is not None
            console.raw(f"\n{error_message}")
            return 1

        try:
            commit_msg_file.write_text(formatted_message)
        except OSError as error:
            console.error(f"Failed to write formatted message: {error}")
            return 1

        original_subject = message.split("\n")[0]
        formatted_subject = formatted_message.split("\n")[0]

        if original_subject != formatted_subject:
            console.success("Commit message formatted!")
            console.raw(f"   Before: {original_subject}")
            console.raw(f"   After:  {formatted_subject}")
        else:
            console.success("Commit message is valid (no changes needed).")

        return 0

    def _format_error_message(self, subject: str) -> str:
        types_list = ", ".join(sorted(self.allowed_types.keys()))
        return f"""Invalid commit message format: '{subject}'

Expected format: Type[(scope)]: description

Examples:
  feat: add user authentication        → ✨ Add user authentication
  fix(api): correct validation         → 🐛(api) Correct validation
  docs: update guide                   → 📚 Update guide
  refactor(handlers): change callback  → 🧹(handlers) Change callback

Allowed types (case-insensitive): {types_list}
"""

    def _format_type_error(self, commit_type: str) -> str:
        types_with_emoji = [
            f"  • {t} {info.get('emoji', '')}: {info['description']}" for t, info in sorted(self.allowed_types.items())
        ]
        types_formatted = "\n".join(types_with_emoji)

        return f"""Invalid commit type: '{commit_type}'

The type '{commit_type}' is not recognized. Please use one of the allowed types below
or update commits_check_config.yaml to add your custom type.

Allowed types (case-insensitive):
{types_formatted}

Example: If you want to add '{commit_type}', add this to commits_check_config.yaml:

  {commit_type.capitalize()}:
    description: Your description here.
    emoji: 🎯
"""


def check_commit_file(commit_msg_file: Path, config_path: Path) -> int:
    """Validate and emojify the given commit message file. Returns the hook exit code."""
    try:
        formatter = CommitMessageFormatter(config_path)
    except CommitConfigError as error:
        console.error(str(error))
        return 1
    return formatter.format_file(commit_msg_file)
