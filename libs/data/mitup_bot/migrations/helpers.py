from string import Template

import sqlalchemy as sa
import structlog
from alembic import op

log = structlog.get_logger(__name__)

CREATED_TRIGGER_TEMPLATE = """
DROP TRIGGER IF EXISTS ${table_name}_created_time_timestamp ON $table_name;
CREATE TRIGGER ${table_name}_created_time_timestamp
BEFORE INSERT ON $table_name
FOR EACH ROW
EXECUTE PROCEDURE set_created_time();
"""

UPDATED_TRIGGER_TEMPLATE = """
DROP TRIGGER IF EXISTS ${table_name}_updated_time_timestamp ON $table_name;
CREATE TRIGGER ${table_name}_updated_time_timestamp
BEFORE INSERT OR UPDATE ON $table_name
FOR EACH ROW
EXECUTE PROCEDURE set_updated_time();
"""


def add_created_time_trigger(table_name: str):
    trigger = Template(CREATED_TRIGGER_TEMPLATE)
    op.execute(trigger.substitute(table_name=table_name))


def add_updated_time_trigger(table_name: str):
    trigger = Template(UPDATED_TRIGGER_TEMPLATE)
    op.execute(trigger.substitute(table_name=table_name))


def remove_created_time_trigger(table_name: str):
    op.execute(f"DROP TRIGGER IF EXISTS {table_name}_created_time_timestamp ON {table_name}")


def remove_updated_time_trigger(table_name: str):
    op.execute(f"DROP TRIGGER IF EXISTS {table_name}_updated_time_timestamp ON {table_name}")


def execute_bulk(revision: str, statement_tag: str, statement: str | sa.Executable) -> int:
    """Run a bulk data mutation and record how many rows it changed, returning that count.

    Alembic reports only that a revision ran, so a statement that matched nothing and one that
    rewrote every row are indistinguishable afterwards — which is what makes a mass rewrite of user
    content unauditable. `statement_tag` is a short stable name for the statement, so a revision
    holding several mutations stays readable. Requires a live connection; not usable in offline mode.
    """
    result = op.get_bind().execute(sa.text(statement) if isinstance(statement, str) else statement)
    log.info("Migration data mutation", revision=revision, statement=statement_tag, rows=result.rowcount)
    return result.rowcount
