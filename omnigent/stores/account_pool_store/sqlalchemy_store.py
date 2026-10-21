"""SQLAlchemy-backed account pool store."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from omnigent.db.db_models import (
    SqlAccountPool,
    SqlAccountPoolAccount,
    SqlAccountQuotaSnapshot,
    current_workspace_id,
)
from omnigent.db.utils import (
    get_or_create_engine,
    make_named_managed_session_maker,
    now_epoch,
    run_write_transaction,
)
from omnigent.entities.account_pool import (
    HOME_KIND_GENERIC,
    AccountPool,
    AccountPoolAccount,
    AccountQuota,
    AccountQuotaWindow,
)
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.stores.account_pool_store import AccountPoolStore, normalize_account_pool_harness

#: Metadata keys that must never be persisted — an account row points at a
#: credential home, it does not carry the credential.
_SECRET_KEY_MARKERS = ("token", "key", "secret", "password", "credential")


def _sanitize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Drop secret-looking keys from an account's annotations.

    :param metadata: The annotations to sanitize.
    :returns: The annotations without any key that looks like a credential.
    """
    if not metadata:
        return {}
    return {
        key: value
        for key, value in metadata.items()
        if not any(marker in str(key).lower() for marker in _SECRET_KEY_MARKERS)
    }


def _decode_metadata(raw: str | None) -> dict[str, Any]:
    """Unpack a stored metadata blob into a mapping.

    :param raw: The stored JSON blob, or ``None``.
    :returns: The decoded mapping, or an empty dict when there is none.
    """
    if not raw:
        return {}
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _to_account_entity(row: SqlAccountPoolAccount) -> AccountPoolAccount:
    """Map an account row to its entity.

    :param row: The stored account row.
    :returns: The account entity.
    """
    return AccountPoolAccount(
        id=row.id,
        pool_id=row.pool_id,
        home_path=row.home_path,
        home_kind=row.home_kind or HOME_KIND_GENERIC,
        enabled=bool(row.enabled),
        created_at=row.created_at,
        updated_at=row.updated_at,
        metadata=_decode_metadata(row.metadata_json),
    )


def _to_pool_entity(row: SqlAccountPool, accounts: list[AccountPoolAccount]) -> AccountPool:
    """Map a pool row and its accounts to the pool entity.

    :param row: The stored pool row.
    :param accounts: The pool's accounts.
    :returns: The pool entity.
    """
    return AccountPool(
        id=row.id,
        name=row.name,
        harness=row.harness,
        description=row.description,
        created_at=row.created_at,
        updated_at=row.updated_at,
        accounts=accounts,
    )


def _to_quota_entity(row: SqlAccountQuotaSnapshot) -> AccountQuota:
    """Map a quota snapshot row to its entity.

    :param row: The stored snapshot row.
    :returns: The quota entity.
    """
    return AccountQuota(
        status=row.status,
        five_hour=AccountQuotaWindow(
            remaining_pct=row.five_hour_remaining_pct,
            status="known" if row.five_hour_remaining_pct is not None else "unknown",
            reset_at=row.five_hour_reset_at,
        ),
        weekly=AccountQuotaWindow(
            remaining_pct=row.weekly_remaining_pct,
            status="known" if row.weekly_remaining_pct is not None else "unknown",
            reset_at=row.weekly_reset_at,
        ),
        model_family=row.model_family,
        observed_at=row.observed_at,
    )


class SqlAlchemyAccountPoolStore(AccountPoolStore):
    """
    SQLAlchemy-backed implementation of the account pool store.

    Persists pools, accounts, and quota snapshots in a relational database.
    Every query is scoped by the ambient workspace id.
    """

    def __init__(self, storage_location: str) -> None:
        """
        Initialize the SQLAlchemy account pool store.

        :param storage_location: SQLAlchemy database URI, e.g.
            ``"sqlite:///chat.db"``.
        """
        super().__init__(storage_location)
        self._engine = get_or_create_engine(storage_location)
        self._session = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.account_pool_store",
        )
        self._session_immediate = make_named_managed_session_maker(
            self._engine,
            query_name_prefix="omnigent.account_pool_store",
            immediate=True,
        )

    # ── Pools ────────────────────────────────────────────────────────────

    def create_pool(
        self,
        pool_id: str,
        name: str,
        harness: str,
        description: str | None = None,
    ) -> AccountPool:
        """Insert a new pool."""
        created_at = now_epoch()

        def write(session: Session) -> AccountPool:
            existing = session.execute(
                select(SqlAccountPool.id).where(
                    SqlAccountPool.workspace_id == current_workspace_id(),
                    SqlAccountPool.name == name,
                )
            ).first()
            if existing is not None:
                raise OmnigentError(
                    f"An account pool named {name!r} already exists",
                    code=ErrorCode.ALREADY_EXISTS,
                )
            row = SqlAccountPool(
                id=pool_id,
                name=name,
                harness=harness,
                description=description,
                created_at=created_at,
                updated_at=None,
            )
            session.add(row)
            session.flush()
            return _to_pool_entity(row, [])

        return run_write_transaction(self._session_immediate, "insert_account_pool", write)

    def _accounts_for(self, session: Session, pool_id: str) -> list[AccountPoolAccount]:
        """Load a pool's accounts, ordered by id.

        :param session: The active SQLAlchemy session.
        :param pool_id: The pool whose accounts are wanted.
        :returns: The pool's accounts.
        """
        rows = (
            session.execute(
                select(SqlAccountPoolAccount)
                .where(SqlAccountPoolAccount.workspace_id == current_workspace_id())
                .where(SqlAccountPoolAccount.pool_id == pool_id)
                .order_by(SqlAccountPoolAccount.id)
            )
            .scalars()
            .all()
        )
        return [_to_account_entity(row) for row in rows]

    def get_pool(self, pool_id: str) -> AccountPool | None:
        """Return a pool by id, with its accounts."""
        with self._session("select_account_pool_by_id") as session:
            row = session.get(SqlAccountPool, (current_workspace_id(), pool_id))
            if row is None:
                return None
            return _to_pool_entity(row, self._accounts_for(session, row.id))

    def get_pool_by_name(self, name: str) -> AccountPool | None:
        """Return a pool by name, with its accounts."""
        with self._session("select_account_pool_by_name") as session:
            row = session.execute(
                select(SqlAccountPool).where(
                    SqlAccountPool.workspace_id == current_workspace_id(),
                    SqlAccountPool.name == name,
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return _to_pool_entity(row, self._accounts_for(session, row.id))

    def get_pool_for_harness(self, harness: str | None) -> AccountPool | None:
        """Return the pool that serves a harness family."""
        if not harness:
            return None
        normalized = normalize_account_pool_harness(harness)
        with self._session("select_account_pool_by_harness") as session:
            rows = (
                session.execute(
                    select(SqlAccountPool)
                    .where(SqlAccountPool.workspace_id == current_workspace_id())
                    .order_by(SqlAccountPool.name)
                )
                .scalars()
                .all()
            )
            for row in rows:
                if normalize_account_pool_harness(row.harness) == normalized:
                    return _to_pool_entity(row, self._accounts_for(session, row.id))
            for row in rows:
                if row.name == "default":
                    return _to_pool_entity(row, self._accounts_for(session, row.id))
        return None

    def list_pools(self) -> list[AccountPool]:
        """List every pool with its accounts, ordered by name."""
        with self._session("list_account_pools") as session:
            rows = (
                session.execute(
                    select(SqlAccountPool)
                    .where(SqlAccountPool.workspace_id == current_workspace_id())
                    .order_by(SqlAccountPool.name)
                )
                .scalars()
                .all()
            )
            return [_to_pool_entity(row, self._accounts_for(session, row.id)) for row in rows]

    def update_pool(
        self,
        pool_id: str,
        *,
        name: str | None = None,
        harness: str | None = None,
        description: str | None = None,
    ) -> AccountPool:
        """Update a pool's mutable fields."""

        def write(session: Session) -> AccountPool:
            row = session.get(SqlAccountPool, (current_workspace_id(), pool_id))
            if row is None:
                raise OmnigentError(
                    f"Account pool {pool_id!r} does not exist",
                    code=ErrorCode.NOT_FOUND,
                )
            if name is not None:
                clash = session.execute(
                    select(SqlAccountPool.id).where(
                        SqlAccountPool.workspace_id == current_workspace_id(),
                        SqlAccountPool.name == name,
                        SqlAccountPool.id != pool_id,
                    )
                ).first()
                if clash is not None:
                    raise OmnigentError(
                        f"An account pool named {name!r} already exists",
                        code=ErrorCode.ALREADY_EXISTS,
                    )
                row.name = name
            if harness is not None:
                row.harness = harness
            if description is not None:
                row.description = description
            row.updated_at = now_epoch()
            session.flush()
            return _to_pool_entity(row, self._accounts_for(session, row.id))

        return run_write_transaction(self._session_immediate, "update_account_pool", write)

    def delete_pool(self, pool_id: str) -> bool:
        """Delete a pool and its accounts."""

        def write(session: Session) -> bool:
            row = session.get(SqlAccountPool, (current_workspace_id(), pool_id))
            if row is None:
                return False
            session.execute(
                delete(SqlAccountPoolAccount).where(
                    SqlAccountPoolAccount.workspace_id == current_workspace_id(),
                    SqlAccountPoolAccount.pool_id == pool_id,
                )
            )
            session.delete(row)
            return True

        return run_write_transaction(self._session_immediate, "delete_account_pool", write)

    # ── Accounts ─────────────────────────────────────────────────────────

    def create_account(
        self,
        account_id: str,
        pool_id: str,
        home_path: str,
        *,
        home_kind: str = HOME_KIND_GENERIC,
        enabled: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> AccountPoolAccount:
        """Insert a new account into a pool."""
        created_at = now_epoch()
        safe_metadata = _sanitize_metadata(metadata)

        def write(session: Session) -> AccountPoolAccount:
            pool = session.get(SqlAccountPool, (current_workspace_id(), pool_id))
            if pool is None:
                raise OmnigentError(
                    f"Account pool {pool_id!r} does not exist",
                    code=ErrorCode.NOT_FOUND,
                )
            clash = session.execute(
                select(SqlAccountPoolAccount.id).where(
                    SqlAccountPoolAccount.workspace_id == current_workspace_id(),
                    SqlAccountPoolAccount.id == account_id,
                )
            ).first()
            if clash is not None:
                raise OmnigentError(
                    f"An account with id {account_id!r} already exists",
                    code=ErrorCode.ALREADY_EXISTS,
                )
            row = SqlAccountPoolAccount(
                id=account_id,
                pool_id=pool_id,
                home_path=home_path,
                home_kind=home_kind,
                enabled=enabled,
                created_at=created_at,
                updated_at=None,
                metadata_json=json.dumps(safe_metadata, separators=(",", ":"))
                if safe_metadata
                else None,
            )
            session.add(row)
            session.flush()
            return _to_account_entity(row)

        return run_write_transaction(self._session_immediate, "insert_account_pool_account", write)

    def get_account(self, account_id: str) -> AccountPoolAccount | None:
        """Return an account by id."""
        with self._session("select_account_pool_account_by_id") as session:
            row = session.get(SqlAccountPoolAccount, (current_workspace_id(), account_id))
            if row is None:
                return None
            return _to_account_entity(row)

    def list_accounts(self, pool_id: str | None = None) -> list[AccountPoolAccount]:
        """List accounts, optionally restricted to one pool."""
        with self._session("list_account_pool_accounts") as session:
            stmt = select(SqlAccountPoolAccount).where(
                SqlAccountPoolAccount.workspace_id == current_workspace_id()
            )
            if pool_id is not None:
                stmt = stmt.where(SqlAccountPoolAccount.pool_id == pool_id)
            rows = session.execute(stmt.order_by(SqlAccountPoolAccount.id)).scalars().all()
            return [_to_account_entity(row) for row in rows]

    def update_account(
        self,
        account_id: str,
        *,
        home_path: str | None = None,
        enabled: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AccountPoolAccount:
        """Update an account's mutable fields."""
        safe_metadata = _sanitize_metadata(metadata) if metadata is not None else None

        def write(session: Session) -> AccountPoolAccount:
            row = session.get(SqlAccountPoolAccount, (current_workspace_id(), account_id))
            if row is None:
                raise OmnigentError(
                    f"Account {account_id!r} does not exist",
                    code=ErrorCode.NOT_FOUND,
                )
            if home_path is not None:
                row.home_path = home_path
            if enabled is not None:
                row.enabled = enabled
            if safe_metadata is not None:
                row.metadata_json = json.dumps(safe_metadata, separators=(",", ":"))
            row.updated_at = now_epoch()
            session.flush()
            return _to_account_entity(row)

        return run_write_transaction(self._session_immediate, "update_account_pool_account", write)

    def delete_account(self, account_id: str) -> bool:
        """Delete an account."""

        def write(session: Session) -> bool:
            row = session.get(SqlAccountPoolAccount, (current_workspace_id(), account_id))
            if row is None:
                return False
            session.delete(row)
            return True

        return run_write_transaction(self._session_immediate, "delete_account_pool_account", write)

    # ── Quota snapshots ──────────────────────────────────────────────────

    def save_quota_snapshot(self, account_id: str, quota: AccountQuota) -> None:
        """Record the last observed quota of an account."""
        family = quota.model_family or "default"
        snapshot_id = f"{account_id}:{family}"
        observed_at = quota.observed_at if quota.observed_at is not None else now_epoch()

        def write(session: Session) -> None:
            row = session.get(
                SqlAccountQuotaSnapshot,
                (current_workspace_id(), snapshot_id),
            )
            if row is None:
                row = SqlAccountQuotaSnapshot(
                    id=snapshot_id,
                    account_id=account_id,
                    model_family=family,
                    status=quota.status,
                    five_hour_remaining_pct=quota.five_hour.remaining_pct,
                    weekly_remaining_pct=quota.weekly.remaining_pct,
                    five_hour_reset_at=quota.five_hour.reset_at,
                    weekly_reset_at=quota.weekly.reset_at,
                    observed_at=observed_at,
                )
                session.add(row)
            else:
                row.status = quota.status
                row.five_hour_remaining_pct = quota.five_hour.remaining_pct
                row.weekly_remaining_pct = quota.weekly.remaining_pct
                row.five_hour_reset_at = quota.five_hour.reset_at
                row.weekly_reset_at = quota.weekly.reset_at
                row.observed_at = observed_at
            session.flush()

        run_write_transaction(self._session_immediate, "save_account_quota_snapshot", write)

    def get_quota_snapshot(
        self, account_id: str, model_family: str | None = None
    ) -> AccountQuota | None:
        """Return the last observed quota of an account."""
        with self._session("select_account_quota_snapshot") as session:
            rows = (
                session.execute(
                    select(SqlAccountQuotaSnapshot).where(
                        SqlAccountQuotaSnapshot.workspace_id == current_workspace_id(),
                        SqlAccountQuotaSnapshot.account_id == account_id,
                    )
                )
                .scalars()
                .all()
            )
            if not rows:
                return None
            if model_family:
                for row in rows:
                    if row.model_family == model_family:
                        return _to_quota_entity(row)
            newest = max(rows, key=lambda row: row.observed_at)
            return _to_quota_entity(newest)

    def list_quota_snapshots(self) -> dict[str, AccountQuota]:
        """Return the last observed quota of every account."""
        with self._session("list_account_quota_snapshots") as session:
            rows = (
                session.execute(
                    select(SqlAccountQuotaSnapshot).where(
                        SqlAccountQuotaSnapshot.workspace_id == current_workspace_id()
                    )
                )
                .scalars()
                .all()
            )
        newest: dict[str, SqlAccountQuotaSnapshot] = {}
        for row in rows:
            current = newest.get(row.account_id)
            if current is None or row.observed_at > current.observed_at:
                newest[row.account_id] = row
        return {account_id: _to_quota_entity(row) for account_id, row in newest.items()}
