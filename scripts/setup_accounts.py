#!/usr/bin/env python3
"""Provision harness subscription accounts from a declarative spec file.

The spec file lists the pools and their accounts::

    pools:
      codex-native:
        harness: codex-native
        description: Codex subscription accounts
        accounts:
          - id: codex-acc1
            home: /home/dat/.codex
            kind: codex_home
          - id: codex-acc2
            home: /home/dat/.codex-acc2
            kind: codex_home

The script writes to the same account pool store the server and the clients
read, so the accounts become selectable without a restart. It is idempotent:
an account that already exists with the same home path is reported as
skipped, not recreated. A home path that does not exist is reported as
failed, because a session launched with it could not authenticate.

Usage::

    python scripts/setup_accounts.py accounts.yaml --config ~/.config/omnigent/server.yaml
    python scripts/setup_accounts.py accounts.yaml --db-uri sqlite:////tmp/accounts.db --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

# The account pool store lives in the omnigent package, so the repo root has
# to be importable when the script runs from a checkout.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from omnigent.entities.account_pool import (
    HOME_KIND_CODEX,
    HOME_KIND_GEMINI,
    HOME_KIND_GENERIC,
)

#: Credential home kinds a spec may name.
HOME_KINDS = (HOME_KIND_CODEX, HOME_KIND_GEMINI, HOME_KIND_GENERIC)


def load_spec(path: Path) -> dict[str, Any]:
    """
    Load a spec file (JSON or YAML).

    :param path: The spec file path.
    :returns: The parsed mapping.
    :raises ValueError: when the file is not a mapping.
    """
    text = path.read_text(encoding="utf-8")
    data: Any
    if path.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        import yaml

        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not contain a mapping")
    return data


def normalize_pools(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Normalize the spec's pool declarations into a flat list.

    Accepts both a mapping of pool name to pool body and a list of pool
    objects with a ``name`` key.

    :param spec: The parsed spec file.
    :returns: One dict per pool, with ``name``, ``harness``, ``description``,
        and ``accounts``.
    """
    raw_pools = spec.get("pools") or {}
    pools: list[dict[str, Any]] = []
    if isinstance(raw_pools, dict):
        for name, body in raw_pools.items():
            body = body if isinstance(body, dict) else {}
            accounts = body.get("accounts")
            if not isinstance(accounts, list):
                accounts = []
            pools.append(
                {
                    "name": str(name),
                    "harness": str(body.get("harness") or name),
                    "description": body.get("description"),
                    "accounts": accounts,
                }
            )
    elif isinstance(raw_pools, list):
        for body in raw_pools:
            if not isinstance(body, dict):
                continue
            name = body.get("name")
            if not name:
                continue
            accounts = body.get("accounts")
            pools.append(
                {
                    "name": str(name),
                    "harness": str(body.get("harness") or name),
                    "description": body.get("description"),
                    "accounts": accounts if isinstance(accounts, list) else [],
                }
            )
    return pools


def account_home(row: dict[str, Any]) -> tuple[str | None, str]:
    """
    Read the credential home of a spec account row.

    :param row: The spec row.
    :returns: The home path and its kind, ``(None, kind)`` when the row
        carries no home.
    """
    for key, kind in (
        ("home", HOME_KIND_GENERIC),
        ("home_path", HOME_KIND_GENERIC),
        ("codex_home", HOME_KIND_CODEX),
        ("gemini_dir", HOME_KIND_GEMINI),
    ):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            declared = str(row.get("kind") or "").strip()
            if declared in HOME_KINDS:
                kind = declared
            return value.strip(), kind
    kind = str(row.get("kind") or HOME_KIND_GENERIC)
    return None, kind if kind in HOME_KINDS else HOME_KIND_GENERIC


def resolve_db_uri(config_path: str | None, db_uri: str | None) -> str:
    """
    Resolve the database URI the account inventory lives in.

    :param config_path: A server config file to read ``database_uri`` from.
    :param db_uri: An explicit database URI that wins over the config file.
    :returns: The database URI.
    """
    if db_uri:
        return db_uri
    if config_path:
        import yaml

        with open(config_path, encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
        if isinstance(config, dict):
            configured = config.get("database_uri")
            if isinstance(configured, str) and configured.strip():
                return configured.strip()
    from omnigent.cli import _default_db_uri

    return _default_db_uri()


def main(argv: list[str] | None = None) -> int:
    """
    Run the provisioning script.

    :param argv: The command-line arguments, or ``None`` for ``sys.argv``.
    :returns: The process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", help="Spec file (YAML or JSON).")
    parser.add_argument("--config", default=None, help="Server config file to read.")
    parser.add_argument("--db-uri", default=None, help="Database URI of the account inventory.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would happen without writing.",
    )
    args = parser.parse_args(argv)

    spec_path = Path(args.spec).expanduser()
    try:
        spec = load_spec(spec_path)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    pools = normalize_pools(spec)
    if not pools:
        print(f"{spec_path} declares no pools", file=sys.stderr)
        return 1

    from omnigent.stores.account_pool_store.sqlalchemy_store import (
        SqlAlchemyAccountPoolStore,
    )

    store = SqlAlchemyAccountPoolStore(resolve_db_uri(args.config, args.db_uri))

    created_count = 0
    skipped_count = 0
    failed_count = 0

    for pool_spec in pools:
        name = pool_spec["name"]
        pool = store.get_pool_by_name(name)
        if pool is None:
            if args.dry_run:
                print(f"would create pool {name!r} (harness {pool_spec['harness']!r})")
                pool_id = "<dry-run>"
            else:
                pool = store.create_pool(
                    uuid.uuid4().hex,
                    name,
                    pool_spec["harness"],
                    pool_spec.get("description"),
                )
                pool_id = pool.id
                print(f"created pool {name!r} (id: {pool_id})")
        else:
            pool_id = pool.id
            print(f"pool {name!r} exists (id: {pool_id})")

        for row in pool_spec["accounts"]:
            if not isinstance(row, dict):
                continue
            account_id = row.get("id") or row.get("name")
            if not isinstance(account_id, str) or not account_id.strip():
                print(f"  skipped: account row without an id in pool {name!r}")
                failed_count += 1
                continue
            account_id = account_id.strip()
            home, kind = account_home(row)
            if home is None:
                print(f"  skipped: account {account_id!r} has no home path")
                failed_count += 1
                continue
            resolved = str(Path(home).expanduser().resolve())
            if not Path(resolved).is_dir():
                print(f"  failed: account {account_id!r} home {resolved!r} does not exist")
                failed_count += 1
                continue

            existing = store.get_account(account_id)
            if existing is not None and existing.home_path == resolved:
                print(f"  skipped: account {account_id!r} already points at {resolved}")
                skipped_count += 1
                continue
            if args.dry_run:
                print(f"  would add account {account_id!r} -> {resolved} ({kind})")
                created_count += 1
                continue
            if existing is not None:
                store.update_account(account_id, home_path=resolved, enabled=True)
            else:
                store.create_account(
                    account_id,
                    pool_id,
                    resolved,
                    home_kind=kind,
                    enabled=True,
                )
            print(f"  added account {account_id!r} -> {resolved} ({kind})")
            created_count += 1

    print(
        f"{'would add' if args.dry_run else 'added'} {created_count} account(s), "
        f"skipped {skipped_count}, failed {failed_count}"
    )
    return 1 if failed_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
