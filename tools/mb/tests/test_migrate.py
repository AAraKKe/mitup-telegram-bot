from pathlib import Path

import pytest
from mb import console, migrate_ops
from mb.main import app
from typer.testing import CliRunner

cli = CliRunner()

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


@pytest.fixture(autouse=True)
def plain_console(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pin a wide console so long status lines are not soft-wrapped mid-assertion.
    monkeypatch.setenv("COLUMNS", "200")
    console.configure(plain=True)


def combined(capsys: pytest.CaptureFixture[str]) -> str:
    captured = capsys.readouterr()
    return captured.out + captured.err


def test_valid_migrations(capsys: pytest.CaptureFixture[str]):
    assert migrate_ops.validate_migration_graph(str(MIGRATIONS_DIR / "valid")) == 0
    output = combined(capsys)
    # rev1 is the base migration (appears once); rev2 is both a child and its own node.
    assert output.count("rev1 Migration 1") == 1
    assert output.count("rev2 Migration 2") == 2


def test_invalid_migrations_with_branching(capsys: pytest.CaptureFixture[str]):
    assert migrate_ops.validate_migration_graph(str(MIGRATIONS_DIR / "invalid_branches")) == 1
    output = combined(capsys)
    assert output.count("rev1 Migration 1") == 1
    assert output.count("rev2 Migration 2") == 2
    assert output.count("rev3 Migration 3") == 2
    assert "Branching migrations found!" in output


def test_invalid_migrations_disconnected(capsys: pytest.CaptureFixture[str]):
    assert migrate_ops.validate_migration_graph(str(MIGRATIONS_DIR / "invalid_disconnected")) == 1
    assert "Revision 'rev3' is referenced by another revision but it doesn't exist" in combined(capsys)


def test_revision_hash_is_stable():
    revision = migrate_ops.Revision(revision="abc", description="")
    assert isinstance(hash(revision), int)
    assert hash(revision) == hash(revision)


def test_build_migration_graph_rejects_non_string_down_revision():
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR / "invalid_non_string_down"))

    with pytest.raises(RuntimeError, match="Only strings are allowed"):
        migrate_ops.build_migration_graph(config)


def test_command_accepts_revisions_path_option(capsys: pytest.CaptureFixture[str]):
    result = cli.invoke(app, ["db", "migrate", "validate", "-p", str(MIGRATIONS_DIR / "valid")])
    assert result.exit_code == 0


def test_command_without_revisions_path_uses_alembic_ini(monkeypatch: pytest.MonkeyPatch):
    from alembic.config import Config as AlembicConfig

    captured: list[AlembicConfig] = []

    def fake_build(config: AlembicConfig) -> migrate_ops.Revision:
        captured.append(config)
        root = migrate_ops.Revision(revision="Base", description="")
        root.child_revisions.append(migrate_ops.Revision(revision="rev1", description="Migration 1"))
        return root

    monkeypatch.setattr(migrate_ops, "build_migration_graph", fake_build)

    result = cli.invoke(app, ["db", "migrate", "validate"])

    assert result.exit_code == 0
    assert len(captured) == 1
    assert captured[0].config_file_name == "alembic.ini"
