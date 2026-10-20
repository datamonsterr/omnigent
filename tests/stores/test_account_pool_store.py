"""Tests for :class:`SqlAlchemyAccountPoolStore`.

Exercises pool and account CRUD, harness lookup, quota snapshots, and the
secret-stripping of account annotations against a real SQLite database.
"""

from __future__ import annotations

import pytest

from omnigent.entities.account_pool import (
    HOME_KIND_CODEX,
    HOME_KIND_GEMINI,
    AccountQuota,
    AccountQuotaWindow,
)
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.account_pool_store.sqlalchemy_store import SqlAlchemyAccountPoolStore


@pytest.fixture()
def store(db_uri: str) -> SqlAlchemyAccountPoolStore:
    """A fresh account pool store backed by the test database.

    :param db_uri: Per-test SQLite URI from the root conftest fixture.
    :returns: A ready-to-use store.
    """
    return SqlAlchemyAccountPoolStore(db_uri)


def test_create_pool_and_read_back(store: SqlAlchemyAccountPoolStore) -> None:
    """``create_pool`` stores the pool and echoes its fields."""
    pool = store.create_pool("pool-codex", "codex-native", "codex-native", "Codex accounts")
    assert pool.id == "pool-codex"
    assert pool.name == "codex-native"
    assert pool.harness == "codex-native"
    assert pool.description == "Codex accounts"
    assert pool.created_at > 0

    fetched = store.get_pool("pool-codex")
    assert fetched is not None
    assert fetched.name == "codex-native"


def test_pool_name_is_unique(store: SqlAlchemyAccountPoolStore) -> None:
    """A second pool with the same name is rejected."""
    store.create_pool("pool-a", "codex-native", "codex-native")
    with pytest.raises(OmnigentError) as excinfo:
        store.create_pool("pool-b", "codex-native", "codex-native")
    assert excinfo.value.code == ErrorCode.ALREADY_EXISTS


def test_pool_lookup_by_harness_normalizes_the_spelling(
    store: SqlAlchemyAccountPoolStore,
) -> None:
    """``codex``, ``codex-native``, and ``codex-sdk`` resolve to one pool."""
    store.create_pool("pool-codex", "codex-native", "codex-native")
    for spelling in ("codex-native", "codex", "codex-sdk"):
        pool = store.get_pool_for_harness(spelling)
        assert pool is not None, spelling
        assert pool.id == "pool-codex"


def test_pool_lookup_falls_back_to_the_default_pool(
    store: SqlAlchemyAccountPoolStore,
) -> None:
    """A harness with no pool of its own resolves the ``default`` pool."""
    store.create_pool("pool-default", "default", "codex-native")
    pool = store.get_pool_for_harness("claude-native")
    assert pool is not None
    assert pool.id == "pool-default"


def test_account_crud(store: SqlAlchemyAccountPoolStore) -> None:
    """An account is created, updated, listed, and deleted."""
    pool = store.create_pool("pool-codex", "codex-native", "codex-native")
    account = store.create_account(
        "codex-acc1",
        pool.id,
        "/tmp/codex-acc1",
        home_kind=HOME_KIND_CODEX,
    )
    assert account.id == "codex-acc1"
    assert account.pool_id == pool.id
    assert account.enabled is True

    listed = store.list_accounts(pool.id)
    assert [item.id for item in listed] == ["codex-acc1"]

    updated = store.update_account("codex-acc1", home_path="/tmp/other", enabled=False)
    assert updated.home_path == "/tmp/other"
    assert updated.enabled is False

    assert store.delete_account("codex-acc1") is True
    assert store.get_account("codex-acc1") is None
    assert store.delete_account("codex-acc1") is False


def test_account_id_is_unique(store: SqlAlchemyAccountPoolStore) -> None:
    """Two accounts cannot share an id."""
    pool = store.create_pool("pool-codex", "codex-native", "codex-native")
    store.create_account("codex-acc1", pool.id, "/tmp/a", home_kind=HOME_KIND_CODEX)
    with pytest.raises(OmnigentError) as excinfo:
        store.create_account("codex-acc1", pool.id, "/tmp/b", home_kind=HOME_KIND_CODEX)
    assert excinfo.value.code == ErrorCode.ALREADY_EXISTS


def test_account_requires_an_existing_pool(store: SqlAlchemyAccountPoolStore) -> None:
    """An account cannot be created in a pool that does not exist."""
    with pytest.raises(OmnigentError) as excinfo:
        store.create_account("codex-acc1", "nope", "/tmp/a", home_kind=HOME_KIND_CODEX)
    assert excinfo.value.code == ErrorCode.NOT_FOUND


def test_account_annotations_drop_secret_keys(store: SqlAlchemyAccountPoolStore) -> None:
    """A credential-looking annotation never reaches the row."""
    pool = store.create_pool("pool-codex", "codex-native", "codex-native")
    store.create_account(
        "codex-acc1",
        pool.id,
        "/tmp/a",
        home_kind=HOME_KIND_CODEX,
        metadata={"api_token": "secret", "note": "keep"},
    )
    account = store.get_account("codex-acc1")
    assert account is not None
    assert account.metadata == {"note": "keep"}


def test_delete_pool_removes_its_accounts(store: SqlAlchemyAccountPoolStore) -> None:
    """Deleting a pool takes its accounts with it."""
    pool = store.create_pool("pool-codex", "codex-native", "codex-native")
    store.create_account("codex-acc1", pool.id, "/tmp/a", home_kind=HOME_KIND_CODEX)
    assert store.delete_pool(pool.id) is True
    assert store.get_pool(pool.id) is None
    assert store.get_account("codex-acc1") is None


def test_update_pool_rejects_a_taken_name(store: SqlAlchemyAccountPoolStore) -> None:
    """Renaming a pool onto an existing name is rejected."""
    store.create_pool("pool-a", "codex-native", "codex-native")
    store.create_pool("pool-b", "antigravity-native", "antigravity-native")
    with pytest.raises(OmnigentError) as excinfo:
        store.update_pool("pool-b", name="codex-native")
    assert excinfo.value.code == ErrorCode.ALREADY_EXISTS


def test_update_missing_pool_raises(store: SqlAlchemyAccountPoolStore) -> None:
    """Updating an unknown pool raises not-found."""
    with pytest.raises(OmnigentError) as excinfo:
        store.update_pool("nope", name="x")
    assert excinfo.value.code == ErrorCode.NOT_FOUND


def test_quota_snapshot_round_trip(store: SqlAlchemyAccountPoolStore) -> None:
    """A quota snapshot is written and read back per account."""
    pool = store.create_pool("pool-codex", "codex-native", "codex-native")
    store.create_account("codex-acc1", pool.id, "/tmp/a", home_kind=HOME_KIND_CODEX)
    quota = AccountQuota(
        status="known",
        five_hour=AccountQuotaWindow(remaining_pct=80.0, status="known"),
        weekly=AccountQuotaWindow(remaining_pct=60.0, status="known"),
        model_family="codex",
        observed_at=1_700_000_000,
    )
    store.save_quota_snapshot("codex-acc1", quota)

    fetched = store.get_quota_snapshot("codex-acc1")
    assert fetched is not None
    assert fetched.five_hour.remaining_pct == 80.0
    assert fetched.weekly.remaining_pct == 60.0
    assert fetched.model_family == "codex"
    assert fetched.is_known() is True

    snapshots = store.list_quota_snapshots()
    assert "codex-acc1" in snapshots


def test_quota_snapshot_for_an_unknown_account(store: SqlAlchemyAccountPoolStore) -> None:
    """An account with no snapshot reads as no snapshot."""
    assert store.get_quota_snapshot("nope") is None


def test_gemini_home_kind_is_preserved(store: SqlAlchemyAccountPoolStore) -> None:
    """The credential home kind round-trips."""
    pool = store.create_pool("pool-agy", "antigravity-native", "antigravity-native")
    account = store.create_account("agy-acc1", pool.id, "/tmp/agy", home_kind=HOME_KIND_GEMINI)
    assert account.home_kind == HOME_KIND_GEMINI
    fetched = store.get_account("agy-acc1")
    assert fetched is not None
    assert fetched.home_kind == HOME_KIND_GEMINI
