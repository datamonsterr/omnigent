"""Account pool store — persists the harness subscription account inventory.

An account pool groups the subscription accounts that serve one harness
family (Codex, Antigravity, …). The pool owns the accounts; the session
create path selects one account out of the pool that matches the session's
harness, and the clients read the same rows to offer an account choice.

The store owns the ``account_pools``, ``account_pool_accounts``, and
``account_quota_snapshots`` tables. An account row stores a *path* to a
credential home directory — never a token, password, or key.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from omnigent.entities.account_pool import (
    HOME_KIND_GENERIC,
    AccountPool,
    AccountPoolAccount,
    AccountQuota,
)


def normalize_account_pool_harness(harness: str | None) -> str:
    """Return the harness family shared by pool lookup and pin validation.

    :param harness: A harness spelling, e.g. ``"codex-native"``.
    :returns: Its family name, or an empty string when none was supplied.
    """
    if not harness:
        return ""
    from omnigent.harness_aliases import canonicalize_harness

    normalized = canonicalize_harness(harness.strip().lower()) or ""
    normalized = normalized.removeprefix("native-")
    for suffix in ("-native", "-sdk", "_sdk", "-ui"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
    return normalized


class AccountPoolStore(ABC):
    """
    Abstract base for account pool persistence.

    Manages pools, their accounts, and the last observed quota per account.
    All reads and writes are scoped by the ambient workspace id, so a
    multi-tenant deployment never sees another workspace's accounts.
    """

    def __init__(self, storage_location: str) -> None:
        """
        Initialize the account pool store.

        :param storage_location: Backend-specific storage URI, e.g.
            ``"sqlite:///chat.db"`` for SQLAlchemy.
        """
        self.storage_location = storage_location

    # ── Pools ────────────────────────────────────────────────────────────

    @abstractmethod
    def create_pool(
        self,
        pool_id: str,
        name: str,
        harness: str,
        description: str | None = None,
    ) -> AccountPool:
        """
        Insert a new pool.

        :param pool_id: Pre-generated unique pool id.
        :param name: Human-readable pool name, unique per workspace.
        :param harness: The harness family the pool serves, e.g.
            ``"codex-native"``.
        :param description: Optional free-text description.
        :returns: The newly created :class:`AccountPool`.
        :raises OmnigentError: ``ALREADY_EXISTS`` when the workspace already
            has a pool with this name.
        """
        ...

    @abstractmethod
    def get_pool(self, pool_id: str) -> AccountPool | None:
        """
        Return a pool by id.

        :param pool_id: The pool id to look up.
        :returns: The pool with its accounts, or ``None`` when not found.
        """
        ...

    @abstractmethod
    def get_pool_by_name(self, name: str) -> AccountPool | None:
        """
        Return a pool by name.

        :param name: The pool name to look up.
        :returns: The pool with its accounts, or ``None`` when not found.
        """
        ...

    @abstractmethod
    def get_pool_for_harness(self, harness: str | None) -> AccountPool | None:
        """
        Return the pool that serves a harness family.

        The harness spelling is normalized (``codex-native`` and ``codex``
        resolve to the same pool) and the ``default`` pool is the fallback.

        :param harness: The harness id or family to resolve.
        :returns: The matching pool, or ``None`` when the harness has no pool.
        """
        ...

    @abstractmethod
    def list_pools(self) -> list[AccountPool]:
        """
        List every pool with its accounts, ordered by name.

        :returns: The workspace's pools.
        """
        ...

    @abstractmethod
    def update_pool(
        self,
        pool_id: str,
        *,
        name: str | None = None,
        harness: str | None = None,
        description: str | None = None,
    ) -> AccountPool:
        """
        Update a pool's mutable fields.

        :param pool_id: The pool to update.
        :param name: New pool name, or ``None`` to keep the current one.
        :param harness: New harness family, or ``None`` to keep it.
        :param description: New description, or ``None`` to keep it.
        :returns: The updated pool.
        :raises OmnigentError: ``NOT_FOUND`` when the pool does not exist.
        """
        ...

    @abstractmethod
    def delete_pool(self, pool_id: str) -> bool:
        """
        Delete a pool and its accounts.

        :param pool_id: The pool to delete.
        :returns: ``True`` when a pool was deleted, ``False`` when it did
            not exist.
        """
        ...

    # ── Accounts ─────────────────────────────────────────────────────────

    @abstractmethod
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
        """
        Insert a new account into a pool.

        :param account_id: The account id, unique per workspace.
        :param pool_id: The pool that owns the account.
        :param home_path: Absolute path to the credential home directory.
        :param home_kind: Which credential home the path is.
        :param enabled: Whether the account may be selected for a session.
        :param metadata: Free-form non-secret annotations.
        :returns: The newly created :class:`AccountPoolAccount`.
        :raises OmnigentError: ``NOT_FOUND`` when the pool does not exist,
            ``ALREADY_EXISTS`` when the account id is taken.
        """
        ...

    @abstractmethod
    def get_account(self, account_id: str) -> AccountPoolAccount | None:
        """
        Return an account by id.

        :param account_id: The account id to look up.
        :returns: The account, or ``None`` when not found.
        """
        ...

    @abstractmethod
    def list_accounts(self, pool_id: str | None = None) -> list[AccountPoolAccount]:
        """
        List accounts, optionally restricted to one pool.

        :param pool_id: The pool to list, or ``None`` for every account.
        :returns: The accounts, ordered by id.
        """
        ...

    @abstractmethod
    def update_account(
        self,
        account_id: str,
        *,
        home_path: str | None = None,
        enabled: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AccountPoolAccount:
        """
        Update an account's mutable fields.

        :param account_id: The account to update.
        :param home_path: New credential home path, or ``None`` to keep it.
        :param enabled: New enabled flag, or ``None`` to keep it.
        :param metadata: New annotations, or ``None`` to keep them.
        :returns: The updated account.
        :raises OmnigentError: ``NOT_FOUND`` when the account does not exist.
        """
        ...

    @abstractmethod
    def delete_account(self, account_id: str) -> bool:
        """
        Delete an account.

        :param account_id: The account to delete.
        :returns: ``True`` when an account was deleted, ``False`` when it did
            not exist.
        """
        ...

    # ── Quota snapshots ──────────────────────────────────────────────────

    @abstractmethod
    def save_quota_snapshot(self, account_id: str, quota: AccountQuota) -> None:
        """
        Record the last observed quota of an account.

        :param account_id: The account the snapshot describes.
        :param quota: The observed quota.
        """
        ...

    @abstractmethod
    def get_quota_snapshot(
        self, account_id: str, model_family: str | None = None
    ) -> AccountQuota | None:
        """
        Return the last observed quota of an account.

        :param account_id: The account to read.
        :param model_family: The model family to prefer, e.g. ``"gemini"``.
            ``None`` accepts any family.
        :returns: The snapshot, or ``None`` when the account has none.
        """
        ...

    @abstractmethod
    def list_quota_snapshots(self) -> dict[str, AccountQuota]:
        """
        Return the last observed quota of every account.

        :returns: A mapping of account id to its most recent snapshot.
        """
        ...
