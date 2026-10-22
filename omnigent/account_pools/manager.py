"""Account pool selection and reservation manager.

The manager answers one question at session-create time: *which account of
the pool that serves this harness should this session run under?* The pool
data comes from the account pool store, so the inventory is durable and
editable at runtime; the ranking is unchanged from the configuration-driven
implementation it replaces.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from omnigent.account_pools.labels import (
    ACCOUNT_HOME_LABEL_KEYS,
    ACCOUNT_ID_LABEL_KEY,
    ACCOUNT_POOL_LABEL_KEY,
    CODEX_HOME_LABEL_KEY,
    GEMINI_DIR_LABEL_KEY,
    LEGACY_ACCOUNT_ID_LABEL_KEY,
)
from omnigent.account_pools.quota import AccountQuota, QuotaTarget, quota_target_for
from omnigent.entities.account_pool import QUOTA_FRESHNESS_SECONDS, AccountPool, AccountPoolAccount
from omnigent.stores.account_pool_store import normalize_account_pool_harness

_logger = logging.getLogger(__name__)

#: The agent spec ``executor.config`` key that names the agent's default account.
AGENT_ACCOUNT_CONFIG_KEY = "account_id"


class AccountPoolExhaustedError(Exception):
    """Raised when all accounts in a pool are exhausted, blocked, or unavailable."""


class AccountNotFoundError(ValueError):
    """Raised when an explicitly pinned account cannot be found in pool."""


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
        """Whether the reservation is older than its TTL."""
        return time.monotonic() - self.created_at > self.ttl_seconds


class AccountReservationTracker:
    """Thread-safe and asyncio-safe reservation tracker for pending launches."""

    def __init__(self, default_ttl_seconds: float = 60.0) -> None:
        """
        Initialize the reservation tracker.

        :param default_ttl_seconds: How long a reservation stays held.
        """
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
        """
        Hold a reservation for an account.

        :param account_id: The account being reserved.
        :param pool_name: The pool the account belongs to.
        :param reservation_id: Optional explicit reservation id.
        :returns: The reservation handle.
        """
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
        """
        Drop a reservation.

        :param handle_or_id: The handle or its id.
        :returns: ``True`` when a reservation was dropped.
        """
        rid = (
            handle_or_id.reservation_id
            if isinstance(handle_or_id, ReservationHandle)
            else str(handle_or_id)
        )
        with self._lock:
            return self._reservations.pop(rid, None) is not None

    def count_pending(self, account_id: str) -> int:
        """
        Count the pending reservations held for an account.

        :param account_id: The account to count.
        :returns: The number of live reservations.
        """
        with self._lock:
            self._prune_expired_locked()
            return sum(1 for h in self._reservations.values() if h.account_id == account_id)

    def clear(self) -> None:
        """Drop every reservation."""
        with self._lock:
            self._reservations.clear()


class AccountPoolManager:
    """Selects the account a session runs under.

    The manager holds no inventory of its own: pools and accounts come from
    the store on every call, so an account added or removed at runtime takes
    effect on the next session create.
    """

    def __init__(
        self,
        store: Any,
        conversation_store: Any | None = None,
        reservation_tracker: AccountReservationTracker | None = None,
        quota_provider: Callable[[str, QuotaTarget], AccountQuota] | None = None,
        codex_selection_policy: str = "load_first",
    ) -> None:
        """
        Initialize the manager.

        :param store: The account pool store to read pools and accounts from.
        :param conversation_store: The conversation store, used to count the
            sessions an account is already serving.
        :param reservation_tracker: The reservation tracker, or ``None`` for
            a fresh one.
        :param quota_provider: The quota probe, or ``None`` for the default
            inference-free probes.
        :param codex_selection_policy: Codex auto ranking: ``load_first`` (default)
            or ``quota_first``. Other harnesses and explicit pins are unchanged.
        """
        if codex_selection_policy not in ("load_first", "quota_first"):
            raise ValueError("codex_selection_policy must be load_first or quota_first")
        self.codex_selection_policy = codex_selection_policy
        self._selection_lock = threading.RLock()
        self.store = store
        self.conversation_store = conversation_store
        self.reservation_tracker = reservation_tracker or AccountReservationTracker()
        if quota_provider is None:
            from omnigent.account_pools.quota import get_default_quota_provider

            self.quota_provider = get_default_quota_provider()
        else:
            self.quota_provider = quota_provider

    def count_active_sessions(self, account_id: str) -> int:
        """Count the active sessions running on an account.

        :param account_id: The account to count for.
        :returns: The number of active sessions, or ``0`` when the count
            cannot be established.
        """
        conversation_store = self.conversation_store
        if conversation_store is None:
            return 0
        from omnigent.stores.conversation_store import runner_seen_is_fresh

        account = self.store.get_account(account_id)
        homes = (
            (CODEX_HOME_LABEL_KEY, account.home_path)
            if account is not None and account.home_path
            else (None, None)
        )

        count = 0
        now = int(time.time())
        after = None
        while True:
            page = conversation_store.list_conversations(
                limit=1000, after=after, kind=None, include_archived=False
            )
            for conv in page.data:
                labels = conv.labels or {}
                identity = labels.get(ACCOUNT_ID_LABEL_KEY) or labels.get(
                    LEGACY_ACCOUNT_ID_LABEL_KEY
                )
                if identity is None and homes[0] is not None and labels.get(homes[0]):
                    if Path(labels[homes[0]]).expanduser().resolve() == Path(homes[1]).resolve():
                        identity = account_id
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

    def get_quota(
        self,
        account: AccountPoolAccount,
        model: str | None = None,
        *,
        probe: bool = True,
        retain_unavailable: bool = False,
    ) -> AccountQuota:
        """Read the last known quota of an account.

        A fresh snapshot in the store is used as-is, so a session create does
        not pay for a probe on every call. When there is no fresh snapshot the
        account is probed (unless ``probe`` is off) and the result is written
        back to the store, so the clients can show it too.

        :param account: The account to read.
        :param model: The model the session would run, used to pick the
            Antigravity quota family.
        :param probe: Whether to probe when no fresh snapshot exists.
        :param retain_unavailable: Keep prior unavailability on failed refresh;
            enabled only by quota-first auto selection.
        :returns: The account's quota; unknown when the probe fails.
        """
        target = quota_target_for(account)
        family: str | None = None
        if target.gemini_dir is not None:
            model_name = (model or "").lower()
            family = (
                "claude"
                if "claude" in model_name
                else "gemini"
                if "gemini" in model_name
                else None
            )
            target.metadata = {**target.metadata, "quota_family": family}
        # The snapshot is per account AND model family: an Antigravity account
        # reports a different quota for Gemini than for Claude.
        stored = self.store.get_quota_snapshot(account.id, family)
        if stored is not None and stored.is_fresh(int(time.time())):
            if not retain_unavailable or (
                stored.observed_at is not None and stored.observed_at <= int(time.time())
            ):
                return stored
        if not probe:
            return stored if stored is not None else AccountQuota()
        try:
            quota = self.quota_provider(account.id, target)
        except Exception:  # noqa: BLE001 - a failed probe leaves the quota unknown
            quota = AccountQuota()
        if not quota.observed_at:
            quota.observed_at = int(time.time())
        if quota.model_family is None:
            quota.model_family = target.metadata.get("quota_family") or "default"
        if (
            retain_unavailable
            and stored is not None
            and stored.is_exhausted()
            and not (
                quota.is_known()
                and quota.observed_at is not None
                and quota.is_fresh(int(time.time()))
                and quota.observed_at <= int(time.time())
            )
        ):
            # An unknown refresh cannot clear evidence of exhaustion or failed auth.
            return stored
        # A snapshot write never blocks selection.
        with contextlib.suppress(Exception):
            self.store.save_quota_snapshot(account.id, quota)
        return quota

    def select_account(
        self,
        pool: AccountPool,
        model: str | None = None,
    ) -> tuple[AccountPoolAccount, ReservationHandle]:
        """Select the account a session should run under.

        The ranking is: fewest active sessions (including pending launch
        reservations), then known quota before unknown quota, then greatest
        remaining five-hour quota, then greatest remaining weekly quota, then
        the account id. Codex's opt-in ``quota_first`` policy ranks fresh known
        quota, five-hour and weekly remaining quota, then load and account id.

        :param pool: The pool to select from.
        :param model: The model the session would run, used to pick the
            quota family.
        :returns: The chosen account and its reservation handle.
        :raises AccountPoolExhaustedError: when no account is eligible.
        """
        quota_first = (
            self.codex_selection_policy == "quota_first"
            and normalize_account_pool_harness(pool.harness) == "codex"
        )
        accounts = [account for account in pool.accounts if account.enabled]
        # The probes run in parallel: each one can take a couple of seconds
        # (a Codex app-server round trip), and a pool holds several accounts.
        quotas: dict[str, AccountQuota] = {}
        if accounts:
            with ThreadPoolExecutor(max_workers=min(len(accounts), 8)) as pool_executor:
                results = list(
                    pool_executor.map(
                        lambda account: self.get_quota(
                            account, model=model, retain_unavailable=quota_first
                        ),
                        accounts,
                    )
                )
            quotas = {account.id: quota for account, quota in zip(accounts, results, strict=True)}
        with self._selection_lock:
            if not accounts:
                raise AccountPoolExhaustedError(
                    f"Account pool {pool.name!r} has no enabled accounts"
                )

            eligible: list[tuple[Any, AccountPoolAccount]] = []
            for account in accounts:
                quota = quotas[account.id]
                if quota.is_exhausted():
                    continue

                active_db = self.count_active_sessions(account.id)
                pending = self.reservation_tracker.count_pending(account.id)
                total_active = active_db + pending

                is_known = quota.is_known()
                if quota_first:
                    is_known = (
                        is_known
                        and quota.observed_at is not None
                        and 0 <= int(time.time()) - quota.observed_at <= QUOTA_FRESHNESS_SECONDS
                    )
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
                    (not is_known, -five_rem, -weekly_rem, total_active, account.id)
                    if quota_first
                    else (total_active, not is_known, -five_rem, -weekly_rem, account.id)
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


def _home_for_account(account: AccountPoolAccount) -> str | None:
    """Return the credential home path an account carries.

    :param account: The account to read.
    :returns: The home path, or ``None`` when the account has none.
    """
    return account.home_path or None


def await_session_account_visible(
    conversation_store: Any,
    conversation_id: str,
    account_id: str | None,
    timeout_seconds: float = 5.0,
) -> bool:
    """Wait until a new session's row is visible to the account counter.

    The counter reads the session's labels, so a reservation must not be
    released until the row *and* its account label are readable — otherwise
    a concurrent create can miss both the reservation and the row and pick
    the same account twice.

    :param conversation_store: The conversation store to read from.
    :param conversation_id: The new session's id.
    :param account_id: The account id the session was assigned, or ``None``
        when the session runs unassigned.
    :param timeout_seconds: How long to wait before giving up.
    :returns: ``True`` when the row became visible in time.
    """
    if conversation_store is None or not account_id:
        return False
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    while time.monotonic() < deadline:
        try:
            conv = conversation_store.get_conversation(conversation_id)
        except Exception:  # noqa: BLE001 - a read error is a miss, not a failure
            conv = None
        if conv is not None and (conv.labels or {}).get(ACCOUNT_ID_LABEL_KEY) == account_id:
            return True
        time.sleep(0.05)
    return False


def prepare_session_account(
    initial_labels: dict[str, str],
    harness: str | None,
    sub_agent_name: str | None,
    manager: AccountPoolManager,
    conversation_store: Any | None = None,
    model: str | None = None,
    allow_stale_default: bool = False,
) -> ReservationHandle | None:
    """Assign the account a new session runs under.

    An explicit pin always wins. Otherwise the pool that serves the
    session's harness picks the account by the manager's ranking. The chosen
    account's credential home is written onto the session labels, which is
    how the runner learns which credential home to launch with.

    :param initial_labels: The session's labels, updated in place.
    :param harness: The session's harness, used to resolve the pool.
    :param sub_agent_name: The sub-agent name, used to resolve the pool.
    :param manager: The account pool manager.
    :param conversation_store: The conversation store, when the manager has
        none.
    :param allow_stale_default: Allow an unavailable saved agent default to fall back.
    :param model: The model the session would run.
    :returns: The reservation handle when an account was reserved, else
        ``None``.
    :raises ValueError: when a pin is unknown, disabled, incompatible, exhausted,
        or conflicts with a pinned home.
    :raises AccountPoolExhaustedError: when the pool has no eligible account.
    """
    if conversation_store is not None and manager.conversation_store is None:
        manager.conversation_store = conversation_store

    pinned_id = initial_labels.get(ACCOUNT_ID_LABEL_KEY) or initial_labels.get(
        LEGACY_ACCOUNT_ID_LABEL_KEY
    )
    pool = manager.store.get_pool_for_harness(harness) or manager.store.get_pool_by_name(
        sub_agent_name or ""
    )

    pinned_homes = {
        key: initial_labels[key] for key in ACCOUNT_HOME_LABEL_KEYS if initial_labels.get(key)
    }

    def home_matches(account: AccountPoolAccount) -> bool:
        homes = {
            CODEX_HOME_LABEL_KEY: account.home_path if account.home_kind == "codex_home" else None,
            GEMINI_DIR_LABEL_KEY: account.home_path if account.home_kind == "gemini_dir" else None,
        }
        return all(
            homes[key] is not None
            and Path(value).expanduser().resolve() == Path(str(homes[key])).resolve()
            for key, value in pinned_homes.items()
        )

    pinned_account = manager.store.get_account(pinned_id) if pinned_id else None
    pinned_pool = manager.store.get_pool(pinned_account.pool_id) if pinned_account else None
    wrong_harness = (
        pinned_pool is not None
        and harness is not None
        and normalize_account_pool_harness(pinned_pool.harness)
        != normalize_account_pool_harness(harness)
    )
    if pinned_id and (
        pinned_account is None
        or not pinned_account.enabled
        or pinned_pool is None
        or wrong_harness
    ):
        if pinned_account is None:
            unavailable_reason = "unknown"
        elif not pinned_account.enabled:
            unavailable_reason = "disabled"
        else:
            unavailable_reason = f"incompatible with harness {harness!r}"
        if not allow_stale_default:
            raise AccountNotFoundError(f"Pinned account {pinned_id!r} is {unavailable_reason}")
        # A stale pin (removed or disabled account) must not launch without a
        # credential home, so the pool's own pick takes over.
        _logger.warning(
            "Pinned account %r is %s; falling back to automatic selection",
            pinned_id,
            unavailable_reason,
        )
        initial_labels.pop(ACCOUNT_ID_LABEL_KEY, None)
        initial_labels.pop(LEGACY_ACCOUNT_ID_LABEL_KEY, None)
        pinned_id = None
    if pinned_id and pinned_account is not None:
        if manager.get_quota(pinned_account, model=model).is_exhausted():
            raise ValueError(f"Pinned account {pinned_id!r} is exhausted or unavailable")
        if pinned_homes and not home_matches(pinned_account):
            raise ValueError(f"Pinned account {pinned_id!r} conflicts with its account home")
        home = _home_for_account(pinned_account)
        if home:
            if pinned_account.home_kind == "codex_home":
                initial_labels[CODEX_HOME_LABEL_KEY] = home
            elif pinned_account.home_kind == "gemini_dir":
                initial_labels[GEMINI_DIR_LABEL_KEY] = home
        initial_labels[ACCOUNT_POOL_LABEL_KEY] = (
            pinned_pool.name if pinned_pool is not None else pinned_account.pool_id
        )
        initial_labels[ACCOUNT_ID_LABEL_KEY] = pinned_id
        return None

    if pinned_homes:
        # An explicit native home already fixes identity, even without an account id.
        if pool is not None:
            matches = [acc for acc in pool.accounts if home_matches(acc)]
            if len(matches) == 1:
                initial_labels[ACCOUNT_ID_LABEL_KEY] = matches[0].id
                initial_labels[ACCOUNT_POOL_LABEL_KEY] = pool.name
        return None

    if pool is not None and pool.accounts:
        chosen, handle = manager.select_account(pool, model=model)
        home = _home_for_account(chosen)
        if home:
            if chosen.home_kind == "codex_home":
                initial_labels.setdefault(CODEX_HOME_LABEL_KEY, home)
            elif chosen.home_kind == "gemini_dir":
                initial_labels.setdefault(GEMINI_DIR_LABEL_KEY, home)
        initial_labels[ACCOUNT_ID_LABEL_KEY] = chosen.id
        initial_labels[ACCOUNT_POOL_LABEL_KEY] = pool.name
        return handle

    return None


def seed_agent_account_label(initial_labels: dict[str, str], spec: Any | None) -> bool:
    """Seed a session's account pin from its agent's default account.

    A session-level pin or credential home always wins, so the agent default
    only applies when the session carries neither.

    :param initial_labels: The session's labels, updated in place.
    :param spec: The agent spec whose ``executor.config.account_id`` names the
        default account, or ``None``.
    :returns: Whether the pin came from the saved agent default.
    """
    if spec is None:
        return False
    if any(
        initial_labels.get(key)
        for key in (ACCOUNT_ID_LABEL_KEY, LEGACY_ACCOUNT_ID_LABEL_KEY, *ACCOUNT_HOME_LABEL_KEYS)
    ):
        return False
    config = getattr(getattr(spec, "executor", None), "config", None)
    account_id = config.get(AGENT_ACCOUNT_CONFIG_KEY) if isinstance(config, dict) else None
    if isinstance(account_id, str) and account_id.strip():
        initial_labels[ACCOUNT_ID_LABEL_KEY] = account_id.strip()
        return True
    return False
