"""Persistence layer. Postgres is canonical; SQLite is the test fallback behind the
same SQLAlchemy interface (see CLAUDE.md, layer-6). The Quant Engine never imports
this module."""
