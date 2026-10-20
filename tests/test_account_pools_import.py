"""Tests for importing the legacy YAML account pool block."""

from __future__ import annotations

from pathlib import Path

import pytest

from omnigent.account_pools.import_legacy import import_account_pools_block
from omnigent.entities.account_pool import HOME_KIND_CODEX, HOME_KIND_GEMINI
from omnigent.stores.account_pool_store.sqlalchemy_store import SqlAlchemyAccountPoolStore


@pytest.fixture()
def store(db_uri: str) -> SqlAlchemyAccountPoolStore:
    """A fresh account pool store backed by the test database.

    :param db_uri: Per-test SQLite URI from the root conftest fixture.
    :returns: A ready-to-use store.
    """
    return SqlAlchemyAccountPoolStore(db_uri)


def test_import_creates_pools_and_accounts(
    store: SqlAlchemyAccountPoolStore, tmp_path: Path
) -> None:
    """A legacy block becomes pools and accounts."""
    codex_home = tmp_path / "codex-1"
    codex_home.mkdir()
    (tmp_path / "codex-2").mkdir()
    agy_home = tmp_path / "agy-1"
    agy_home.mkdir()
    report = import_account_pools_block(
        store,
        {
            "codex-native": {
                "accounts": [
                    {"id": "codex-1", "codex_home": str(codex_home)},
                    {"id": "codex-2", "codex_home": str(tmp_path / "codex-2")},
                ]
            },
            "antigravity-native": [
                {"id": "agy-1", "gemini_dir": str(agy_home)},
            ],
        },
    )
    assert report.total_created == 3
    assert report.total_skipped == 0
    assert not report.errors

    codex = store.get_pool_by_name("codex-native")
    assert codex is not None
    assert [account.id for account in codex.accounts] == ["codex-1", "codex-2"]
    assert codex.accounts[0].home_kind == HOME_KIND_CODEX

    agy = store.get_pool_by_name("antigravity-native")
    assert agy is not None
    assert agy.accounts[0].home_kind == HOME_KIND_GEMINI


def test_import_is_idempotent(store: SqlAlchemyAccountPoolStore, tmp_path: Path) -> None:
    """A second import skips the accounts it already created."""
    home = tmp_path / "codex-1"
    home.mkdir()
    block = {
        "codex-native": {
            "accounts": [{"id": "codex-1", "codex_home": str(home)}],
        }
    }
    first = import_account_pools_block(store, block)
    assert first.total_created == 1

    second = import_account_pools_block(store, block)
    assert second.total_created == 0
    assert second.total_skipped == 1
    assert len(store.list_accounts()) == 1


def test_import_drops_secret_keys(store: SqlAlchemyAccountPoolStore, tmp_path: Path) -> None:
    """A legacy entry carrying a token never stores it."""
    home = tmp_path / "codex-1"
    home.mkdir()
    report = import_account_pools_block(
        store,
        {
            "codex-native": {
                "accounts": [
                    {
                        "id": "codex-1",
                        "codex_home": str(home),
                        "api_token": "secret-value",
                    }
                ]
            }
        },
    )
    assert report.pools["codex-native"].dropped_keys == ["api_token"]
    account = store.get_account("codex-1")
    assert account is not None
    assert "secret-value" not in repr(account.metadata)


def test_import_reports_a_missing_home(store: SqlAlchemyAccountPoolStore) -> None:
    """An account whose home does not exist is reported, not imported."""
    report = import_account_pools_block(
        store,
        {"codex-native": {"accounts": [{"id": "codex-1", "codex_home": "/definitely/not/here"}]}},
    )
    assert report.total_created == 0
    assert any("does not exist" in error for error in report.errors)


def test_import_of_an_empty_block_is_a_no_op(store: SqlAlchemyAccountPoolStore) -> None:
    """An absent or malformed block imports nothing."""
    assert import_account_pools_block(store, None).total_created == 0
    assert import_account_pools_block(store, {}).total_created == 0
    assert import_account_pools_block(store, "nope").total_created == 0
    assert store.list_pools() == []
