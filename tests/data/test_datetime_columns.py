import sqlalchemy as sa
from sqlmodel import SQLModel
from sqlmodel.sql.sqltypes import UTCDateTime

import mitup_bot.models  # noqa: F401  registers every table on SQLModel.metadata


def datetime_columns() -> list[sa.Column]:
    return [
        column
        for table in SQLModel.metadata.tables.values()
        for column in table.columns
        if isinstance(getattr(column.type, "impl", column.type), sa.DateTime)
    ]


def test_every_datetime_column_is_utc_aware():
    """A field declared with an explicit `DateTime` accepts naive values and reads back in the database
    session's time zone, so every datetime field must leave its type to be inferred as `UTCDateTime`."""
    columns = datetime_columns()

    assert "meetups.datetime" in {str(column) for column in columns}
    assert [str(column) for column in columns if not isinstance(column.type, UTCDateTime)] == []
