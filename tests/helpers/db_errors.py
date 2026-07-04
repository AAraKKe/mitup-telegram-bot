from types import SimpleNamespace

from sqlalchemy.exc import IntegrityError


class _FakeConstraintError(Exception):
    """Stand-in for the DBAPI error SQLAlchemy wraps as ``IntegrityError.orig``.

    Carries the psycopg-style ``diag.constraint_name`` that ``db.racy_flush`` inspects. When no
    constraint name is given, ``diag`` is left unset so ``getattr(orig, "diag", None)`` returns ``None``.
    """

    diag: SimpleNamespace

    def __init__(self, constraint_name: str | None):
        super().__init__("duplicate key value violates unique constraint")
        if constraint_name is not None:
            self.diag = SimpleNamespace(constraint_name=constraint_name)


def integrity_error(constraint_name: str | None) -> IntegrityError:
    """Build an IntegrityError shaped like the one a Postgres constraint violation produces.

    ``db.racy_flush`` decides whether to swallow a clash by inspecting
    ``exc.orig.diag.constraint_name`` (the psycopg diagnostics), so tests need an ``orig`` that carries
    that attribute. Pass the constraint name to mimic that specific violation, or ``None`` to mimic an
    ``orig`` without diagnostics — the case that must re-raise.
    """
    return IntegrityError("INSERT INTO joined_users", {}, _FakeConstraintError(constraint_name))
