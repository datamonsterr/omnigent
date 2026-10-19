"""Account pool selection and reservation manager."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from omnigent.account_pools.models import (
    ACCOUNT_ID_LABEL_KEY,
    ACCOUNT_POOL_LABEL_KEY,
    CODEX_HOME_LABEL_KEY,
    GEMINI_DIR_LABEL_KEY,
    AccountConfig,
    AccountPool,
    AccountPoolsConfig,
)
from omnigent.account_pools.quota import AccountQuota

if TYPE_CHECKING:
    from omnigent.stores.conversation_store import ConversationStore


class AccountPoolExhaustedError(Exception):
    """Raised when all accounts in a pool are exhausted, blocked, or unavailable."""


class AccountNotFoundError(Exception):
    """Raised when an explicit pinned account cannot be found in pool."""


@dataclass
class ReservationHandle:
    """Reservation handle for pending launch."""

    account_id: str
    pool_name: str
    reservation_id: str
    created_at: float
    ttl_seconds: float = 60.0

    @property
    def is_expired(self) -> bool:
        return time.monotonic() - self.created_at > self.ttl_seconds


class AccountReservationTracker:
    """Thread-safe and asyncio-safe reservation tracker for pending launches."""

    def __init__(self, default_ttl_seconds: float = 60.0) -> None:
        self._default_ttl = default_ttl_seconds
        self._lock = threading.Lock()
        self._reservations: dict[str, ReservationHandle] = {}

    def _prune_expired_locked(self) -> None:
        now = time.monotonic()
        expired = [
            rid for rid, h in self._reservations.items() if (now - h.created_at) > h.ttl_seconds
        ]
        for rid in expired:
            self._reservations.pop(rid, None)

    def reserve(
        self, account_id: str, pool_name: str, reservation_id: str | None = None
    ) -> ReservationHandle:
        with self._lock:
            self._prune_expired_locked()
            rid = reservation_id or str(uuid.uuid4())
            handle = ReservationHandle(
                account_id=account_id,
                pool_name=pool_name,
                reservation_id=rid,
                created_at=time.monotonic(),
                ttl_seconds=self._default_ttl,
            )
            self._reservations[rid] = handle
            return handle

    def release(self, handle_or_id: ReservationHandle | str) -> bool:
        rid = (
            handle_or_id.reservation_id
            if isinstance(handle_or_id, ReservationHandle)
            else str(handle_or_id)
        )
        with self._lock:
            return self._reservations.pop(rid, None) is not None

    def count_pending(self, account_id: str) -> int:
        with self._lock:
            self._prune_expired_locked()
            return sum(1 for h in self._reservations.values() if h.account_id == account_id)

    def clear(self) -> None:
        with self._lock:
            self._reservations.clear()


class AccountPoolManager:
    """Manages account selection, active counting, and environment forwarding."""

    def __init__(
        self,
        config: AccountPoolsConfig | None = None,
        reservation_tracker: AccountReservationTracker | None = None,
        quota_provider: Callable[[str, AccountConfig], AccountQuota] | None = None,
    ) -> None:
        self.config = config or AccountPoolsConfig()
        self.reservation_tracker = reservation_tracker or AccountReservationTracker()
        if quota_provider is None:
            from omnigent.account_pools.quota import get_default_quota_provider

            self.quota_provider = get_default_quota_provider()
        else:
            self.quota_provider = quota_provider

    def count_active_sessions(
        self,
        conversation_store: ConversationStore | None,
        account_id: str,
    ) -> int:
        """Count active sessions (running, waiting, pending) for this account in DB."""
        if conversation_store is None:
            return 0
        from omnigent.stores.conversation_store import runner_seen_is_fresh

        try:
            paged = conversation_store.list_conversations(
                limit=1000,
                kind=None,
                include_archived=False,
            )
            if hasattr(paged, "data") and isinstance(paged.data, (list, tuple)):
                items = paged.data
            elif hasattr(paged, "items") and isinstance(paged.items, (list, tuple)):
                items = paged.items
            elif isinstance(paged, (list, tuple)):
                items = paged
            else:
                items = getattr(paged, "data", getattr(paged, "items", []))
            count = 0
            now = int(time.time())
            for conv in items:
                labels = conv.labels or {}
                conv_acc = labels.get(ACCOUNT_ID_LABEL_KEY) or labels.get("account_id")
                if conv_acc != account_id:
                    continue

                if (
                    getattr(conv, "archived", False)
                    or getattr(conv, "is_archived", False)
                    or getattr(conv, "archived_at", None) is not None
                ):
                    continue

                live_st = getattr(conv, "live_status", None)
                if live_st in ("idle", "stopped", "disconnected", "closed", "failed", "completed"):
                    continue

                runner_seen = getattr(conv, "runner_last_seen", getattr(conv, "last_seen", None))
                if live_st in ("running", "waiting", "pending"):
                    if runner_seen is not None and not runner_seen_is_fresh(runner_seen):
                        continue
                    count += 1
                elif live_st is None:
                    # Session in launch window before runner has reported live_status
                    created_at = getattr(conv, "created_at", None)
                    if created_at is not None and (now - created_at) < 120:
                        count += 1
            return count
        except Exception:  # noqa: BLE001 - fallback to 0 active count on store read failure
            return 0

    def get_quota(self, account: AccountConfig) -> AccountQuota:
        if self.quota_provider is not None:
            try:
                return self.quota_provider(account.id, account)
            except Exception:  # noqa: BLE001 - fallback to default unknown quota on provider failure
                return AccountQuota()
        return AccountQuota()

    def select_account(
        self,
        pool: AccountPool,
        conversation_store: ConversationStore | None = None,
    ) -> tuple[AccountConfig, ReservationHandle]:
        """
        Select account by fewest active sessions, then quota, then ID tie-break.

        1. Fewest active sessions (including pending launch reservations).
        2. Known quota before unknown quota on same session count.
        3. Most remaining subscription quota (5h primary, weekly secondary).
        4. Stable account ID tie-break.
        """
        if not pool.accounts:
            raise AccountPoolExhaustedError(
                f"Account pool {pool.name!r} has no configured accounts"
            )

        eligible: list[tuple[Any, AccountConfig]] = []
        for account in pool.accounts:
            quota = self.get_quota(account)
            if quota.is_exhausted():
                continue

            active_db = self.count_active_sessions(conversation_store, account.id)
            pending = self.reservation_tracker.count_pending(account.id)
            total_active = active_db + pending

            is_known = quota.is_known()
            five_rem = (
                quota.five_hour.remaining_pct
                if (is_known and quota.five_hour.remaining_pct is not None)
                else 0.0
            )
            weekly_rem = (
                quota.weekly.remaining_pct
                if (is_known and quota.weekly.remaining_pct is not None)
                else 0.0
            )

            sort_key = (
                total_active,
                not is_known,
                -five_rem,
                -weekly_rem,
                account.id,
            )
            eligible.append((sort_key, account))

        if not eligible:
            raise AccountPoolExhaustedError(
                f"All accounts in pool {pool.name!r} are exhausted or unavailable"
            )

        eligible.sort(key=lambda item: item[0])
        chosen = eligible[0][1]
        handle = self.reservation_tracker.reserve(chosen.id, pool.name)
        return chosen, handle


def prepare_session_account(
    initial_labels: dict[str, str],
    harness: str | None,
    sub_agent_name: str | None,
    agent_spec: Any | None,
    manager: AccountPoolManager,
    conversation_store: ConversationStore | None = None,
) -> ReservationHandle | None:
    """
    Prepare and forward account assignment for a session create.

    Honors explicit account pins always.
    Selects best account if unpinned and pool is available.
    Forwards codex_home and gemini_dir into initial_labels.
    Returns ReservationHandle if a reservation was acquired, else None.
    """
    pinned_id = initial_labels.get(ACCOUNT_ID_LABEL_KEY) or initial_labels.get("account_id")

    # Check for agent-spec declared account_pools
    cfg = manager.config
    if agent_spec is not None:
        exec_cfg = getattr(getattr(agent_spec, "executor", None), "config", None)
        if isinstance(exec_cfg, dict) and "account_pools" in exec_cfg:
            from omnigent.account_pools.models import parse_account_pools

            spec_pools = parse_account_pools(exec_cfg["account_pools"])
            if spec_pools:
                cfg = spec_pools

    pool = cfg.get_pool_for_harness_or_agent(harness=harness, agent_name=sub_agent_name)

    if pinned_id:
        acc: AccountConfig | None = None
        if pool is not None:
            acc = pool.get_account(pinned_id)
        if acc is None:
            found = cfg.find_account(pinned_id)
            if found is not None:
                acc, pool = found

        if acc is not None:
            if acc.codex_home and CODEX_HOME_LABEL_KEY not in initial_labels:
                initial_labels[CODEX_HOME_LABEL_KEY] = str(acc.codex_home)
            if acc.gemini_dir and GEMINI_DIR_LABEL_KEY not in initial_labels:
                initial_labels[GEMINI_DIR_LABEL_KEY] = str(acc.gemini_dir)
            if pool is not None:
                initial_labels[ACCOUNT_POOL_LABEL_KEY] = pool.name

        initial_labels[ACCOUNT_ID_LABEL_KEY] = pinned_id
        return None

    if pool is not None and pool.accounts:
        chosen, handle = manager.select_account(pool, conversation_store=conversation_store)
        if chosen.codex_home and CODEX_HOME_LABEL_KEY not in initial_labels:
            initial_labels[CODEX_HOME_LABEL_KEY] = str(chosen.codex_home)
        if chosen.gemini_dir and GEMINI_DIR_LABEL_KEY not in initial_labels:
            initial_labels[GEMINI_DIR_LABEL_KEY] = str(chosen.gemini_dir)
        initial_labels[ACCOUNT_ID_LABEL_KEY] = chosen.id
        initial_labels[ACCOUNT_POOL_LABEL_KEY] = pool.name
        return handle

    return None
