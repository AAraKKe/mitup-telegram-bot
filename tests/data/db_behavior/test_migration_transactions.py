import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, event

from mitup_bot.config import DbConfig

pytestmark = pytest.mark.db_test


def test_each_revision_commits_on_its_own(scratch_database: DbConfig):
    alembic_config = AlembicConfig("alembic.ini")
    revisions = list(ScriptDirectory.from_config(alembic_config).walk_revisions())
    commits: list[Connection] = []

    def record_commit(connection: Connection):
        commits.append(connection)

    event.listen(Engine, "commit", record_commit)
    try:
        command.upgrade(alembic_config, "head")
    finally:
        event.remove(Engine, "commit", record_commit)

    assert len(commits) >= len(revisions)
