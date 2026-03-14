import pytest
from click.testing import CliRunner
from rich.console import Console

from mitup_bot.cli.commands import validate_migrations as validate
from mitup_bot.cli.commands.validate_migrations import Revision, build_migration_graph

# Make console not export colors and styles for testing
validate.console = Console(force_interactive=False, force_terminal=False)


def test_valid_migrations():
    runner = CliRunner()

    with validate.console.capture() as capture:
        runner.invoke(validate.cli, args=["-p", "tests/cli/migrations/valid"])

    output = capture.get()

    # Only appears one as the base migration
    assert output.count("rev1 Migration 1") == 1
    # Appears twice as the child of the base and its own
    assert output.count("rev2 Migration 2") == 2


def test_invalid_migrations_with_branching():
    runner = CliRunner()

    with validate.console.capture() as capture:
        result = runner.invoke(validate.cli, args=["-p", "tests/cli/migrations/invalid_branches"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)

    output = capture.get()

    # Only appears one as the base migration
    assert output.count("rev1 Migration 1") == 1
    # Appears twice as the child of the base and its own
    assert output.count("rev2 Migration 2") == 2
    # Appears twice as the child of the base and its own
    assert output.count("rev3 Migration 3") == 2
    assert "Branching migrations found!" in output


def test_invalid_migrations_disconnected():
    runner = CliRunner()

    with validate.console.capture() as capture:
        result = runner.invoke(validate.cli, args=["-p", "tests/cli/migrations/invalid_disconnected"])

    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)

    output = capture.get()

    # The only output is the error since the code will fail when parsing revisions. No tree will be printed
    assert "Revision 'rev3' is referenced by another revision but it doesn't exist" in output


def test_revision_hash_returns_int():
    rev = Revision(revision="abc", description="")
    result = hash(rev)
    assert isinstance(result, int)
    # Hashing twice must return the same value (stability)
    assert hash(rev) == result


def test_build_migration_graph_raises_for_non_string_down_revision():
    """build_migration_graph must raise RuntimeError when down_revision is a tuple (alembic merge style)."""
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", "tests/cli/migrations/invalid_non_string_down")

    with pytest.raises(RuntimeError, match="Only strings are allowed"):
        build_migration_graph(config)


def test_cli_accepts_revisions_path_option():
    """The --revisions-path option must be accepted and used to locate migrations."""
    runner = CliRunner()

    with validate.console.capture() as capture:
        result = runner.invoke(validate.cli, args=["--revisions-path", "tests/cli/migrations/valid"])

    output = capture.get()

    # Invocation with long form --revisions-path must succeed (exit 0) and produce tree output
    assert result.exit_code == 0
    assert "rev1 Migration 1" in output


def test_cli_without_revisions_path_uses_alembic_ini(monkeypatch):
    """When --revisions-path is omitted, Config('alembic.ini') is used (line 79 branch)."""
    from alembic.config import Config as AlembicConfig

    captured: list[AlembicConfig] = []

    def _fake_build(config: AlembicConfig) -> validate.Revision:
        captured.append(config)
        # Return a minimal graph that validate_and_draw processes without branching
        root = validate.Revision(revision="Base", description="")
        child = validate.Revision(revision="rev1", description="Migration 1")
        root.child_revisions.append(child)
        return root

    monkeypatch.setattr(validate, "build_migration_graph", _fake_build)

    runner = CliRunner()
    with validate.console.capture():
        result = runner.invoke(validate.cli, args=[])

    # The CLI must succeed (exit 0) and must have been called with Config("alembic.ini"),
    # not Config() — the alembic.ini branch sets the config file name explicitly.
    assert result.exit_code == 0
    assert len(captured) == 1
    assert captured[0].config_file_name == "alembic.ini"  # confirms the if-branch was taken
