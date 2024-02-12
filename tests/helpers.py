from unittest import mock

from sqlalchemy.dialects import postgresql


def get_querys_from_session(session: mock.MagicMock) -> list[str]:
    return [
        str(call.args[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        for call in session.exec.call_args_list
    ]
