"""Migration ``mn1a2b3c4d5e`` adds the account pool tables.

The harness account inventory moved from a server config block into the
database, so the tables must exist at head with the columns the store reads,
and the downgrade must drop them again.
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command

from omnigent.db.utils import (
    _build_alembic_config,
    clear_engine_cache,
    get_or_create_engine,
)

_TABLES = ("account_pools", "account_pool_accounts", "account_quota_snapshots")


def _table_names(engine: sa.Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names())


def test_tables_at_head(tmp_path: Path) -> None:
    """The three account tables exist at head with their columns."""
    engine = get_or_create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    try:
        inspector = sa.inspect(engine)
        names = _table_names(engine)
        for table in _TABLES:
            assert table in names
        pool_columns = {c["name"] for c in inspector.get_columns("account_pools")}
        assert {"workspace_id", "id", "name", "harness", "description"} <= pool_columns
        account_columns = {c["name"] for c in inspector.get_columns("account_pool_accounts")}
        assert {"id", "pool_id", "home_path", "home_kind", "enabled"} <= account_columns
        snapshot_columns = {c["name"] for c in inspector.get_columns("account_quota_snapshots")}
        assert {"id", "account_id", "model_family", "status"} <= snapshot_columns
    finally:
        clear_engine_cache()


def test_downgrade_drops_the_tables(tmp_path: Path) -> None:
    """The downgrade removes the account tables."""
    uri = f"sqlite:///{tmp_path / 'roundtrip.db'}"
    cfg = _build_alembic_config(uri)
    engine = sa.create_engine(uri)
    try:
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "mn1a2b3c4d5e")
        assert _TABLES[0] in _table_names(engine)
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.downgrade(cfg, "mm1a2b3c4d5e")
        names = _table_names(engine)
        for table in _TABLES:
            assert table not in names
    finally:
        engine.dispose()
        clear_engine_cache()
