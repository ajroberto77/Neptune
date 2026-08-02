"""Alembic environment for the SECURITIES database (prices/dividends/corporate actions).

See alembic/portfolio/env.py's docstring — same template, this DB's Base/models/URL.
"""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context

from neptune.config import settings
from neptune.db.base import SecuritiesBase, make_engine
from neptune.securities import models  # noqa: F401 -- registers ORM classes before
                                        #            target_metadata is read below.

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SecuritiesBase.metadata


def get_url() -> str:
    return settings.securities_url


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = make_engine(get_url())
    try:
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=True,
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
