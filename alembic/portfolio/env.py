"""Alembic environment for the PORTFOLIO database (entities/books/positions/lots).

Reuses the app's own connection-resolution and engine-construction logic instead of
re-deriving it: ``neptune.config.settings.portfolio_url`` (env/.env/fallback precedence)
and ``neptune.db.base.make_engine`` (SQLite pooling/FK-enforcement handling, shared with
the app's own bootstrap path). No ``sqlalchemy.url`` is read from alembic.ini — the URL
always comes from here, so no credential needs to live in a committed file.
"""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context

from neptune.config import settings
from neptune.db.base import PortfolioBase, make_engine
from neptune.db import models  # noqa: F401 -- registers ORM classes on PortfolioBase.metadata
                                #            before target_metadata is read below; without
                                #            this import the metadata is empty and
                                #            autogenerate would try to drop every table.

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = PortfolioBase.metadata


def get_url() -> str:
    return settings.portfolio_url


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
