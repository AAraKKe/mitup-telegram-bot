from collections.abc import Iterator
from contextlib import AbstractContextManager
from typing import Any, LiteralString, cast

import psycopg
from psycopg.rows import dict_row


class RailsReader(AbstractContextManager["RailsReader"]):
    """Read-only cursor over the legacy Rails Postgres database.

    Streams rows in chunks via a server-side cursor so the Lambda doesn't have to
    materialize whole tables in memory.
    """

    def __init__(self, dsn: str, batch_size: int = 1000):
        self._dsn = dsn
        self._batch_size = batch_size
        self._conn: psycopg.Connection | None = None

    def __enter__(self) -> RailsReader:
        self._conn = psycopg.connect(self._dsn, autocommit=False)
        self._conn.read_only = True
        return self

    def __exit__(self, *_: object):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def connection(self) -> psycopg.Connection:
        if self._conn is None:
            raise RuntimeError("RailsReader used outside its context manager")
        return self._conn

    def count(self, table: str) -> int:
        # psycopg types queries as LiteralString to discourage injection; every caller passes
        # constant table names / queries (see the S608 suppressions), so the casts are safe.
        query = cast("LiteralString", f'SELECT COUNT(*) FROM "{table}"')  # noqa: S608 — table name is a constant
        with self.connection.cursor() as cur:
            cur.execute(query)
            row = cur.fetchone()
            assert row is not None, "COUNT(*) always returns a row"
            (n,) = row
            return int(n)

    def stream(self, query: str, params: tuple[object, ...] | None = None) -> Iterator[dict[str, Any]]:
        cursor_name = f"mitup_migrate_{id(self):x}"
        with self.connection.cursor(name=cursor_name, row_factory=dict_row) as cur:
            cur.itersize = self._batch_size
            cur.execute(cast("LiteralString", query), params or ())
            for row in cur:
                yield dict(row)
