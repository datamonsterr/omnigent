"""Migration ``mn1a2b3c4d5e`` adds the account pool tables.

The harness account inventory moved from a server config block into the
database, so the tables must exist at head with the columns the store reads,
and the downgrade must drop them again.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.migration import MigrationContext
from alembic.operations import Operations

from omnigent.db.compression import decode, encode
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
        metadata_column = next(
            column
            for column in inspector.get_columns("account_pool_accounts")
            if column["name"] == "metadata"
        )
        assert isinstance(metadata_column["type"], sa.LargeBinary)
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


@pytest.mark.parametrize("start_revision", ["mo1a2b3c4d5e", "nf12ab34cd56"])
def test_upgrade_from_either_branch_preserves_both_schemas(
    tmp_path: Path, start_revision: str
) -> None:
    """Existing fork and upstream databases converge without losing account pools."""
    uri = f"sqlite:///{tmp_path / 'branches.db'}"
    cfg = _build_alembic_config(uri)
    engine = sa.create_engine(uri)
    try:
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, start_revision)
            if start_revision == "mo1a2b3c4d5e":
                conn.execute(
                    sa.text(
                        "INSERT INTO account_pools (workspace_id, id, name, harness, created_at)"
                        " VALUES (0, 'existing', 'Existing pool', 'codex-native', 1)"
                    )
                )
            command.upgrade(cfg, "head")
            if start_revision == "mo1a2b3c4d5e":
                assert (
                    conn.execute(
                        sa.text("SELECT name FROM account_pools WHERE id = 'existing'")
                    ).scalar_one()
                    == "Existing pool"
                )
        assert set(_TABLES) <= _table_names(engine)
        indexes = sa.inspect(engine).get_indexes("omnigent_conversation_metadata")
        assert "ix_conversation_metadata_host_id" in {index["name"] for index in indexes}
    finally:
        engine.dispose()
        clear_engine_cache()


def test_upgrade_converts_legacy_text_metadata_without_losing_annotations(
    tmp_path: Path,
) -> None:
    """A database at the merged head with the historical TEXT column is repaired."""
    uri = f"sqlite:///{tmp_path / 'legacy-metadata.db'}"
    cfg = _build_alembic_config(uri)
    engine = sa.create_engine(uri)
    metadata = '{"note":"kept through upgrade"}'
    try:
        with engine.begin() as connection:
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "mp1a2b3c4d5e")
            connection.execute(
                sa.text(
                    "INSERT INTO account_pool_accounts "
                    "(workspace_id, id, pool_id, home_path, created_at, metadata) "
                    "VALUES (0, 'codex-1', 'codex', '/accounts/codex-1', 1, :metadata)"
                ),
                {"metadata": metadata},
            )
            assert isinstance(
                next(
                    column
                    for column in sa.inspect(connection).get_columns("account_pool_accounts")
                    if column["name"] == "metadata"
                )["type"],
                sa.Text,
            )
            command.upgrade(cfg, "head")

        column = next(
            column
            for column in sa.inspect(engine).get_columns("account_pool_accounts")
            if column["name"] == "metadata"
        )
        assert isinstance(column["type"], sa.LargeBinary)
        with engine.connect() as connection:
            stored = connection.execute(
                sa.text("SELECT metadata FROM account_pool_accounts WHERE id = 'codex-1'")
            ).scalar_one()
        assert decode(stored) == metadata
    finally:
        engine.dispose()
        clear_engine_cache()


def test_upgrade_keeps_metadata_from_already_binary_deployment(tmp_path: Path) -> None:
    """The repair also accepts databases changed by the deployed migration."""
    uri = f"sqlite:///{tmp_path / 'deployed-metadata.db'}"
    cfg = _build_alembic_config(uri)
    engine = sa.create_engine(uri)
    metadata = '{"note":"already binary"}'
    framed = encode(metadata)
    try:
        with engine.begin() as connection:
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "mp1a2b3c4d5e")
            operations = Operations(MigrationContext.configure(connection))
            with operations.batch_alter_table("account_pool_accounts") as batch:
                batch.alter_column(
                    "metadata",
                    existing_type=sa.Text(),
                    type_=sa.LargeBinary(),
                    existing_nullable=True,
                )
            connection.execute(
                sa.text(
                    "INSERT INTO account_pool_accounts "
                    "(workspace_id, id, pool_id, home_path, created_at, metadata) "
                    "VALUES (0, 'codex-1', 'codex', '/accounts/codex-1', 1, :metadata)"
                ),
                {"metadata": framed},
            )
            command.upgrade(cfg, "head")
            stored = connection.execute(
                sa.text("SELECT metadata FROM account_pool_accounts WHERE id = 'codex-1'")
            ).scalar_one()
        assert stored == framed
        assert decode(stored) == metadata
    finally:
        engine.dispose()
        clear_engine_cache()
