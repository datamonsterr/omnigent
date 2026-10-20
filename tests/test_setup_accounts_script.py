"""Tests for the account provisioning script (``scripts/setup_accounts.py``)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup_accounts.py"


def _load_script() -> Any:
    """Load the provisioning script as a module.

    :returns: The loaded module.
    """
    spec = importlib.util.spec_from_file_location("setup_accounts", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["setup_accounts"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def script() -> Any:
    """The provisioning script module."""
    return _load_script()


def test_spec_normalizes_both_pool_shapes(script: Any) -> None:
    """A pool list and a pool mapping normalize to the same shape."""
    from_list = script.normalize_pools(
        {
            "pools": [
                {
                    "name": "codex-native",
                    "harness": "codex-native",
                    "accounts": [{"id": "a", "home": "/tmp/a"}],
                }
            ]
        }
    )
    from_map = script.normalize_pools(
        {
            "pools": {
                "codex-native": {
                    "harness": "codex-native",
                    "accounts": [{"id": "a", "home": "/tmp/a"}],
                }
            }
        }
    )
    assert from_list[0]["name"] == from_map[0]["name"] == "codex-native"
    assert from_list[0]["accounts"] == from_map[0]["accounts"]


def test_account_home_reads_the_declared_kind(script: Any, tmp_path: Path) -> None:
    """A spec row's `kind` decides the credential home kind."""
    home, kind = script.account_home({"home": str(tmp_path), "kind": "codex_home"})
    assert home == str(tmp_path)
    assert kind == "codex_home"


def test_provisioning_creates_pools_and_accounts(script: Any, tmp_path: Path) -> None:
    """The script creates the pool and its accounts."""
    home = tmp_path / "codex-acc1"
    home.mkdir()
    spec_path = tmp_path / "accounts.yaml"
    spec_path.write_text(
        "pools:\n"
        "  codex-native:\n"
        "    harness: codex-native\n"
        "    accounts:\n"
        f"      - id: codex-acc1\n        home: {home}\n        kind: codex_home\n",
        encoding="utf-8",
    )
    db_uri = f"sqlite:///{tmp_path / 'accounts.db'}"
    exit_code = script.main([str(spec_path), "--db-uri", db_uri])
    assert exit_code == 0

    from omnigent.stores.account_pool_store.sqlalchemy_store import (
        SqlAlchemyAccountPoolStore,
    )

    store = SqlAlchemyAccountPoolStore(db_uri)
    pool = store.get_pool_by_name("codex-native")
    assert pool is not None
    assert [account.id for account in pool.accounts] == ["codex-acc1"]


def test_provisioning_is_idempotent(script: Any, tmp_path: Path) -> None:
    """A second run skips the accounts it already created."""
    home = tmp_path / "codex-acc1"
    home.mkdir()
    spec_path = tmp_path / "accounts.yaml"
    spec_path.write_text(
        "pools:\n"
        "  codex-native:\n"
        "    accounts:\n"
        f"      - id: codex-acc1\n        home: {home}\n        kind: codex_home\n",
        encoding="utf-8",
    )
    db_uri = f"sqlite:///{tmp_path / 'accounts.db'}"
    assert script.main([str(spec_path), "--db-uri", db_uri]) == 0
    assert script.main([str(spec_path), "--db-uri", db_uri]) == 0

    from omnigent.stores.account_pool_store.sqlalchemy_store import (
        SqlAlchemyAccountPoolStore,
    )

    store = SqlAlchemyAccountPoolStore(db_uri)
    assert len(store.list_accounts()) == 1


def test_provisioning_reports_a_missing_home(script: Any, tmp_path: Path) -> None:
    """An account whose home does not exist fails the run."""
    spec_path = tmp_path / "accounts.yaml"
    spec_path.write_text(
        "pools:\n"
        "  codex-native:\n"
        "    accounts:\n"
        "      - id: codex-acc1\n"
        f"        home: {tmp_path / 'missing'}\n",
        encoding="utf-8",
    )
    db_uri = f"sqlite:///{tmp_path / 'accounts.db'}"
    assert script.main([str(spec_path), "--db-uri", db_uri]) == 1
