from string import Template

from alembic import op

CREATED_TRIGGER_TEMPLATE = """
CREATE TRIGGER ${table_name}_created_time_timestamp
BEFORE INSERT ON $table_name
FOR EACH ROW
EXECUTE PROCEDURE set_created_time();
"""

UPDATED_TRIGGER_TEMPLATE = """
CREATE TRIGGER ${table_name}_updated_time_timestamp
BEFORE UPDATE ON $table_name
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
