"""Account pool selection and reservation manager."""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
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
        self._selection_lock = threading.RLock()
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

        count = 0
        now = int(time.time())
        after = None
        while True:
            page = conversation_store.list_conversations(
                limit=1000, after=after, kind=None, include_archived=False
            )
            for conv in page.data:
                labels = conv.labels or {}
                identity = labels.get(ACCOUNT_ID_LABEL_KEY) or labels.get("account_id")
                if identity is None:
                    found = self.config.find_account(account_id)
                    if found is not None:
                        account, _ = found
                        from pathlib import Path

                        for key, home in (
                            (CODEX_HOME_LABEL_KEY, account.codex_home),
                            (GEMINI_DIR_LABEL_KEY, account.gemini_dir),
                        ):
                            if (
                                home is not None
                                and labels.get(key)
                                and Path(labels[key]).expanduser().resolve() == home
                            ):
                                identity = account_id
                                break
                if identity != account_id:
                    continue
                if conv.archived:
                    continue
                if conv.live_status in ("running", "waiting"):
                    if runner_seen_is_fresh(conv.runner_last_seen, now=now):
                        count += 1
                elif conv.live_status in (None, "pending") and 0 <= now - conv.created_at < 120:
                    # Count persisted launches until their first runner status arrives.
                    count += 1
            if not page.has_more:
                return count
            if page.last_id is None or page.last_id == after:
                raise RuntimeError("Conversation pagination did not advance")
            after = page.last_id

    def get_quota(self, account: AccountConfig, model: str | None = None) -> AccountQuota:
        if self.quota_provider is not None:
            if account.gemini_dir is not None:
                model_name = (model or "").lower()
                family = (
                    "claude"
                    if "claude" in model_name
                    else "gemini"
                    if "gemini" in model_name
                    else None
                )
                account = replace(account, metadata={**account.metadata, "quota_family": family})
            try:
                return self.quota_provider(account.id, account)
            except Exception:  # noqa: BLE001 - fallback to default unknown quota on provider failure
                return AccountQuota()
        return AccountQuota()

    def select_account(
        self,
        pool: AccountPool,
        conversation_store: ConversationStore | None = None,
        model: str | None = None,
    ) -> tuple[AccountConfig, ReservationHandle]:
        """
        Select account by fewest active sessions, then quota, then ID tie-break.

        1. Fewest active sessions (including pending launch reservations).
        2. Known quota before unknown quota on same session count.
        3. Most remaining subscription quota (5h primary, weekly secondary).
        4. Stable account ID tie-break.
        """
        quotas = {account.id: self.get_quota(account, model=model) for account in pool.accounts}
        with self._selection_lock:
            if not pool.accounts:
                raise AccountPoolExhaustedError(
                    f"Account pool {pool.name!r} has no configured accounts"
                )

            eligible: list[tuple[Any, AccountConfig]] = []
            for account in pool.accounts:
                quota = quotas[account.id]
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
    model: str | None = None,
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

    pinned_homes = {
        key: initial_labels[key]
        for key in (CODEX_HOME_LABEL_KEY, GEMINI_DIR_LABEL_KEY)
        if initial_labels.get(key)
    }

    def home_matches(account: AccountConfig) -> bool:
        from pathlib import Path

        homes = {
            CODEX_HOME_LABEL_KEY: account.codex_home,
            GEMINI_DIR_LABEL_KEY: account.gemini_dir,
        }
        return all(
            homes[key] is not None and Path(value).expanduser().resolve() == homes[key]
            for key, value in pinned_homes.items()
        )

    if pinned_id:
        found = cfg.find_account(pinned_id)
        if found is not None:
            acc, pinned_pool = found
            if pinned_homes and not home_matches(acc):
                raise ValueError(f"Pinned account {pinned_id!r} conflicts with its account home")
            if acc.codex_home:
                initial_labels[CODEX_HOME_LABEL_KEY] = str(acc.codex_home)
            if acc.gemini_dir:
                initial_labels[GEMINI_DIR_LABEL_KEY] = str(acc.gemini_dir)
            initial_labels[ACCOUNT_POOL_LABEL_KEY] = pinned_pool.name
        initial_labels[ACCOUNT_ID_LABEL_KEY] = pinned_id
        return None

    if pinned_homes:
        # An explicit native home already fixes identity, even without an account ID.
        if pool is not None:
            matches = [acc for acc in pool.accounts if home_matches(acc)]
            if len(matches) == 1:
                initial_labels[ACCOUNT_ID_LABEL_KEY] = matches[0].id
                initial_labels[ACCOUNT_POOL_LABEL_KEY] = pool.name
        return None

    if pool is not None and pool.accounts:
        chosen, handle = manager.select_account(
            pool, conversation_store=conversation_store, model=model
        )
        if chosen.codex_home and CODEX_HOME_LABEL_KEY not in initial_labels:
            initial_labels[CODEX_HOME_LABEL_KEY] = str(chosen.codex_home)
        if chosen.gemini_dir and GEMINI_DIR_LABEL_KEY not in initial_labels:
            initial_labels[GEMINI_DIR_LABEL_KEY] = str(chosen.gemini_dir)
        initial_labels[ACCOUNT_ID_LABEL_KEY] = chosen.id
        initial_labels[ACCOUNT_POOL_LABEL_KEY] = pool.name
        return handle

    return None
