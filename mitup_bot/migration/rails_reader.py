from collections.abc import Iterator
from contextlib import AbstractContextManager
from typing import Any

import psycopg2
import psycopg2.extensions
import psycopg2.extras


class RailsReader(AbstractContextManager["RailsReader"]):
    """Read-only cursor over the legacy Rails Postgres database.

    Streams rows in chunks via a server-side cursor so the Lambda doesn't have to
    materialize whole tables in memory.
    """

    def __init__(self, dsn: str, batch_size: int = 1000):
        self._dsn = dsn
        self._batch_size = batch_size
        self._conn: psycopg2.extensions.connection | None = None

    def __enter__(self) -> RailsReader:
        self._conn = psycopg2.connect(self._dsn)
        self._conn.set_session(readonly=True, autocommit=False)
        return self

    def __exit__(self, *_: object):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def connection(self) -> psycopg2.extensions.connection:
        if self._conn is None:
            raise RuntimeError("RailsReader used outside its context manager")
        return self._conn

    def count(self, table: str) -> int:
        with self.connection.cursor() as cur:
            cur.execute(f'SELECT COUNT(*) FROM "{table}"')  # noqa: S608 — table name is a constant
            (n,) = cur.fetchone()
            return int(n)

    def stream(self, query: str, params: tuple[object, ...] | None = None) -> Iterator[dict[str, Any]]:
        cursor_name = f"mitup_migrate_{id(self):x}"
        with self.connection.cursor(name=cursor_name, cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.itersize = self._batch_size
            cur.execute(query, params or ())
            for row in cur:
                yield dict(row)
