"""Import the legacy YAML account pool block into the account pool store.

The account inventory used to live in a server config block::

    account_pools:
      codex-native:
        accounts:
          - id: codex-acc1
            codex_home: /home/dat/.codex

That block is no longer read by the server. This module converts it into
store rows so a deployment can move its inventory without hand-copying it.
The import is idempotent and never stores a secret.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omnigent.entities.account_pool import (
    HOME_KIND_CODEX,
    HOME_KIND_GEMINI,
    HOME_KIND_GENERIC,
)

#: Legacy account keys that carry a credential home, mapped to the home kind.
_HOME_KEYS = {
    "codex_home": HOME_KIND_CODEX,
    "source_codex_home": HOME_KIND_CODEX,
    "gemini_dir": HOME_KIND_GEMINI,
    "source_gemini_dir": HOME_KIND_GEMINI,
    "home_path": HOME_KIND_GENERIC,
    "path": HOME_KIND_GENERIC,
}

#: Legacy account keys that must never be imported.
_SECRET_KEY_MARKERS = ("token", "key", "secret", "password", "credential")


@dataclass
class PoolImportOutcome:
    """What the import did to one pool.

    :param name: The pool name.
    :param created: Account ids the import created.
    :param skipped: Account ids the import left untouched.
    :param dropped_keys: Secret-looking keys the import dropped.
    """

    name: str
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    dropped_keys: list[str] = field(default_factory=list)


@dataclass
class ImportReport:
    """The result of importing the legacy block.

    :param pools: One outcome per pool.
    :param errors: Human-readable errors, one per pool that failed.
    """

    pools: dict[str, PoolImportOutcome] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def total_created(self) -> int:
        """Total accounts created across every pool."""
        return sum(len(outcome.created) for outcome in self.pools.values())

    @property
    def total_skipped(self) -> int:
        """Total accounts skipped across every pool."""
        return sum(len(outcome.skipped) for outcome in self.pools.values())


def _parse_account_rows(pool_content: Any) -> list[dict[str, Any]]:
    """Extract the account rows of one legacy pool entry.

    :param pool_content: The pool's value from the legacy block.
    :returns: The account rows, empty when the entry holds none.
    """
    if isinstance(pool_content, list):
        return [row for row in pool_content if isinstance(row, dict)]
    if isinstance(pool_content, dict):
        rows = pool_content.get("accounts")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def _home_from_row(row: dict[str, Any]) -> tuple[str | None, str]:
    """Read the credential home out of a legacy account row.

    :param row: The legacy account row.
    :returns: The home path and its kind, or ``(None, kind)`` when the row
        carries no home.
    """
    for key, kind in _HOME_KEYS.items():
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip(), kind
    return None, HOME_KIND_GENERIC


def _secret_keys(row: dict[str, Any]) -> list[str]:
    """List the secret-looking keys of a legacy account row.

    :param row: The legacy account row.
    :returns: The keys that look like credentials.
    """
    return [
        key for key in row if any(marker in str(key).lower() for marker in _SECRET_KEY_MARKERS)
    ]


def import_account_pools_block(store: Any, raw: Any) -> ImportReport:
    """Import a legacy ``account_pools:`` block into the store.

    :param store: The account pool store to write to.
    :param raw: The parsed legacy block, or ``None``.
    :returns: The import report.
    """
    report = ImportReport()
    if not raw or not isinstance(raw, dict):
        return report

    for pool_name, pool_content in raw.items():
        if not isinstance(pool_name, str) or not pool_name.strip():
            continue
        name = pool_name.strip()
        outcome = PoolImportOutcome(name=name)
        report.pools[name] = outcome

        rows = _parse_account_rows(pool_content)
        if not rows:
            continue

        try:
            pool = store.get_pool_by_name(name)
            if pool is None:
                pool = store.create_pool(
                    _new_pool_id(),
                    name,
                    name,
                    None,
                )
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            report.errors.append(f"{name}: could not create the pool ({exc})")
            continue

        for row in rows:
            account_id = row.get("id") or row.get("name")
            if not isinstance(account_id, str) or not account_id.strip():
                continue
            account_id = account_id.strip()
            dropped = _secret_keys(row)
            if dropped:
                outcome.dropped_keys.extend(dropped)

            home_path, home_kind = _home_from_row(row)
            if home_path is None:
                # A legacy entry without a home (a harness that keeps its
                # credentials elsewhere) still names an account, so it is
                # created without one and edited later.
                store.create_account(
                    account_id,
                    pool.id,
                    "",
                    home_kind=HOME_KIND_GENERIC,
                    enabled=True,
                )
                outcome.created.append(account_id)
                continue
            resolved = str(Path(home_path).expanduser().resolve())
            if not Path(resolved).is_dir():
                report.errors.append(
                    f"{name}: account {account_id!r} home path {resolved!r} does not exist"
                )
                continue

            existing = store.get_account(account_id)
            if existing is not None:
                if (
                    existing.home_path == resolved
                    and existing.home_kind == home_kind
                    and existing.enabled
                ):
                    outcome.skipped.append(account_id)
                    continue
                store.update_account(
                    account_id,
                    home_path=resolved,
                    enabled=True,
                )
                outcome.created.append(account_id)
                continue

            store.create_account(
                account_id,
                pool.id,
                resolved,
                home_kind=home_kind,
                enabled=True,
            )
            outcome.created.append(account_id)

    return report


def _new_pool_id() -> str:
    """Return a fresh pool id.

    :returns: A random hex id.
    """
    import uuid

    return uuid.uuid4().hex


__all__ = [
    "ImportReport",
    "PoolImportOutcome",
    "import_account_pools_block",
]
