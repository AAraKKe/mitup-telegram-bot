import logging
from logging.config import fileConfig

import structlog

# Alembic's own progress lines ("Running upgrade X -> Y"). Held at INFO so a migration run stays
# traceable even when the host process raised the root level.
ALEMBIC_LOGGER_NAME = "alembic"

# SQLAlchemy echoes every statement at INFO; a migration run only needs its warnings.
SQLALCHEMY_LOGGER_NAME = "sqlalchemy.engine"


def configure_migration_logging(config_file_name: str | None):
    """Set up logging for an Alembic run without tearing down the host process's pipeline.

    `fileConfig` defaults to `disable_existing_loggers=True`, which sets `.disabled` on every logger
    already instantiated in the process and replaces the root handlers. Structlog's stdlib logger
    factory means each of its loggers is one of those instances, so a host that configured
    structured logging before invoking Alembic would lose every line it logs afterwards — including
    its own failure record. Where that pipeline exists it stays authoritative and Alembic's loggers
    propagate into it; a bare `alembic` CLI run has none, so the ini file applies with the existing
    loggers spared.
    """
    if structlog.is_configured():
        logging.getLogger(ALEMBIC_LOGGER_NAME).setLevel(logging.INFO)
        logging.getLogger(SQLALCHEMY_LOGGER_NAME).setLevel(logging.WARNING)
        return

    if config_file_name is not None:
        fileConfig(config_file_name, disable_existing_loggers=False)
