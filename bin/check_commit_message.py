#!/usr/bin/env python3
"""Custom conventional commit message formatter for mitup-telegram-bot.

This script formats commit messages by replacing conventional commit type prefixes
with emojis defined in commits_check_config.yaml.
"""

import re
import sys
from pathlib import Path

import yaml


class CommitMessageFormatter:
    """Formats commit messages by replacing type prefixes with emojis."""

    # Regex pattern for conventional commit format (case-insensitive type)
    # Format: Type[(scope)]: description
    COMMIT_PATTERN = re.compile(
        r"^(?P<type>[A-Za-z]+)"  # Type (any case)
        r"(?:\((?P<scope>[a-z0-9_\-/, ]+)\))?"  # Optional scope
        r": "  # Colon and space
        r"(?P<description>.+)$",  # Description
        re.IGNORECASE,  # Case insensitive matching
    )

    def __init__(self, config_path: Path):
        """Initialize the formatter with configuration.

        Args:
            config_path: Path to the commits_check_config.yaml file
        """
        self.config_path = config_path
        self.allowed_types = self._load_config()
        # Create case-insensitive lookup
        self.type_lookup = {k.lower(): k for k in self.allowed_types}

    def _load_config(self) -> dict[str, dict]:
        """Load allowed commit types from configuration file.

        Returns:
            Dictionary of allowed commit types with their metadata
        """
        try:
            with open(self.config_path) as f:
                config = yaml.safe_load(f)
                return config.get("additional_commit_types", {})
        except FileNotFoundError:
            print(f"❌ Configuration file not found: {self.config_path}")
            sys.exit(1)
        except yaml.YAMLError as e:
            print(f"❌ Error parsing YAML configuration: {e}")
            sys.exit(1)

    def format_commit_message(self, message: str) -> tuple[str | None, str | None]:
        """Format a commit message by replacing type with emoji.

        Args:
            message: The commit message to format

        Returns:
            Tuple of (error_message, formatted_message)
            If error_message is not None, formatting failed
            If error_message is None, formatted_message contains the result
        """
        # Skip merge commits
        if message.startswith("Merge "):
            return None, message

        # Extract the first line (subject)
        lines = message.strip().split("\n")
        subject = lines[0].strip()

        # Check against pattern
        match = self.COMMIT_PATTERN.match(subject)
        if not match:
            return self._format_error_message(subject), None

        # Validate type (case-insensitive)
        commit_type = match.group("type")
        commit_type_lower = commit_type.lower()

        if commit_type_lower not in self.type_lookup:
            return self._format_type_error(commit_type), None

        # Get the canonical type name and its emoji
        canonical_type = self.type_lookup[commit_type_lower]
        emoji = self.allowed_types[canonical_type].get("emoji", "")

        # Get description and capitalize first letter
        description = match.group("description").strip()
        if not description:
            return "Commit message must have a description after the colon.", None

        # Capitalize first letter of description
        if description[0].islower() and description[0].isalpha():
            description = description[0].upper() + description[1:]

        # Get optional scope
        scope = match.group("scope")

        # Build the formatted subject
        formatted_subject = emoji
        if scope:
            formatted_subject += f"({scope})"
        formatted_subject += f" {description}"

        # Rebuild the full message
        formatted_lines = [formatted_subject]
        if len(lines) > 1:
            # Preserve body and footer
            formatted_lines.extend(lines[1:])

        formatted_message = "\n".join(formatted_lines)
        return None, formatted_message

    def _format_error_message(self, subject: str) -> str:
        """Format a helpful error message for invalid commit format.

        Args:
            subject: The invalid commit subject line

        Returns:
            Formatted error message
        """
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
        """Format error message for invalid commit type.

        Args:
            commit_type: The invalid commit type

        Returns:
            Formatted error message with suggestions
        """
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

    def format_file(self, commit_msg_file: Path) -> int:
        """Format a commit message file by replacing type with emoji.

        Args:
            commit_msg_file: Path to the commit message file

        Returns:
            Exit code (0 for success, 1 for failure)
        """
        try:
            message = commit_msg_file.read_text()
        except FileNotFoundError:
            print(f"❌ Commit message file not found: {commit_msg_file}")
            return 1

        error_message, formatted_message = self.format_commit_message(message)

        if formatted_message is None:
            print("❌ Commit message formatting failed!\n")
            print(error_message)
            return 1

        # Write the formatted message back to the file
        try:
            with open(commit_msg_file, "w") as f:
                f.write(formatted_message)
        except Exception as e:
            print(f"❌ Failed to write formatted message: {e}")
            return 1

        # Show what was done
        original_subject = message.split("\n")[0]
        formatted_subject = formatted_message.split("\n")[0]

        if original_subject != formatted_subject:
            print("✨ Commit message formatted!")
            print(f"   Before: {original_subject}")
            print(f"   After:  {formatted_subject}")
        else:
            print("✅ Commit message is valid (no changes needed)")

        return 0


def find_git_root(start_path: Path) -> Path | None:
    """Find the git repository root by looking for .git directory.

    Args:
        start_path: Path to start searching from

    Returns:
        Path to git root, or None if not found
    """
    current = start_path.resolve()

    # Search up to filesystem root
    while current != current.parent:
        if (current / ".git").exists():
            return current
        current = current.parent

    return None


def main() -> int:
    """Main entry point for the commit message formatter.

    Returns:
        Exit code (0 for success, 1 for failure)
    """
    if len(sys.argv) < 2:
        print("Usage: check_commit_message.py <commit-msg-file>")
        return 1

    commit_msg_file = Path(sys.argv[1])

    # Find the git repository root first
    repo_root = find_git_root(Path.cwd())

    if not repo_root:
        print("❌ Not in a git repository")
        return 1

    # Look for the config file in the repository root
    config_path = repo_root / "commits_check_config.yaml"

    if not config_path.exists():
        print(f"❌ Could not find commits_check_config.yaml in repository root: {repo_root}")
        print("   Expected location: commits_check_config.yaml")
        return 1

    formatter = CommitMessageFormatter(config_path)
    return formatter.format_file(commit_msg_file)


if __name__ == "__main__":
    sys.exit(main())
