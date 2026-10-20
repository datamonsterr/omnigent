"""Account pool entities — persisted in the ``account_pools`` tables.

A harness subscription account is operational state: it has an identity, a
credential home directory, a quota window, and a share of the machine's
running sessions. The pool groups the accounts that serve one harness
family, and the account is the unit the session-create path selects.

An account row stores a *path* to a credential home (``codex_home``,
``gemini_dir``); it never stores a token, password, or key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Credential home kinds an account can carry.
HOME_KIND_CODEX = "codex_home"
HOME_KIND_GEMINI = "gemini_dir"
HOME_KIND_GENERIC = "generic"

#: Quota windows an account can report.
QUOTA_WINDOW_FIVE_HOUR = "five_hour"
QUOTA_WINDOW_WEEKLY = "weekly"

#: A quota snapshot older than this reads as unknown.
QUOTA_FRESHNESS_SECONDS = 120


@dataclass
class AccountQuotaWindow:
    """
    One quota window of an account.

    :param remaining_pct: Remaining percentage of the window, or ``None``
        when the window is unknown.
    :param status: ``"known"``, ``"unknown"``, ``"blocked"``,
        ``"exhausted"``, or ``"unreported"``.
    :param reset_at: Human-readable reset time as reported by the provider,
        or ``None``.
    """

    remaining_pct: float | None = None
    status: str = "unknown"
    reset_at: str | None = None

    def is_known(self) -> bool:
        """Whether this window carries a usable remaining percentage."""
        return self.remaining_pct is not None and self.status in ("known", "unreported")

    def is_exhausted(self) -> bool:
        """Whether this window is blocked or spent."""
        if self.status in ("blocked", "exhausted"):
            return True
        return self.remaining_pct is not None and self.remaining_pct <= 0


@dataclass
class AccountQuota:
    """
    The last observed quota of one account.

    :param status: Overall status — ``"known"``, ``"unknown"``,
        ``"blocked"``, ``"exhausted"``, ``"probe_error"``, or
        ``"auth_failed"``.
    :param five_hour: The rolling five-hour window.
    :param weekly: The weekly window.
    :param model_family: The model family the snapshot belongs to, e.g.
        ``"gemini"`` or ``"claude"``.
    :param observed_at: Unix epoch seconds of the observation, or ``None``
        when the quota was never observed.
    """

    status: str = "unknown"
    five_hour: AccountQuotaWindow = field(default_factory=AccountQuotaWindow)
    weekly: AccountQuotaWindow = field(default_factory=AccountQuotaWindow)
    model_family: str | None = None
    observed_at: int | None = None

    def is_known(self) -> bool:
        """Whether the snapshot carries a usable remaining percentage."""
        if self.status != "known":
            return False
        return self.five_hour.remaining_pct is not None or self.weekly.remaining_pct is not None

    def is_exhausted(self) -> bool:
        """Whether the account is spent on any window."""
        if self.status in ("blocked", "exhausted", "auth_failed"):
            return True
        return self.five_hour.is_exhausted() or self.weekly.is_exhausted()

    def is_fresh(self, now: int) -> bool:
        """Whether the snapshot is recent enough to trust.

        :param now: Unix epoch seconds to compare against.
        :returns: ``True`` when the snapshot was observed within
            :data:`QUOTA_FRESHNESS_SECONDS`.
        """
        if self.observed_at is None:
            return False
        return (now - self.observed_at) <= QUOTA_FRESHNESS_SECONDS


@dataclass
class AccountPoolAccount:
    """
    One subscription account inside a pool.

    :param id: Account id, unique per workspace, e.g. ``"codex-acc1"``.
    :param pool_id: Id of the pool that owns the account.
    :param home_path: Absolute path to the credential home directory.
    :param home_kind: Which credential home the path is — ``"codex_home"``,
        ``"gemini_dir"``, or ``"generic"``.
    :param enabled: Whether the account may be selected for a new session.
    :param created_at: Unix epoch seconds at row creation.
    :param updated_at: Unix epoch seconds of the last write, or ``None``.
    :param metadata: Free-form non-secret annotations.
    """

    id: str
    pool_id: str
    home_path: str
    home_kind: str = HOME_KIND_GENERIC
    enabled: bool = True
    created_at: int = 0
    updated_at: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AccountPool:
    """
    A named group of accounts that serves one harness family.

    :param id: Pool id, unique per workspace.
    :param name: Human-readable pool name, unique per workspace.
    :param harness: The harness family the pool serves, e.g.
        ``"codex-native"``.
    :param description: Optional free-text description.
    :param created_at: Unix epoch seconds at row creation.
    :param updated_at: Unix epoch seconds of the last write, or ``None``.
    :param accounts: The pool's accounts.
    """

    id: str
    name: str
    harness: str
    description: str | None = None
    created_at: int = 0
    updated_at: int | None = None
    accounts: list[AccountPoolAccount] = field(default_factory=list)

    def get_account(self, account_id: str) -> AccountPoolAccount | None:
        """
        Find one account by id.

        :param account_id: The account id to look for.
        :returns: The account, or ``None`` when the pool has no such account.
        """
        for account in self.accounts:
            if account.id == account_id:
                return account
        return None


@dataclass
class AccountUsage:
    """
    Per-account consumption facts for the usage views.

    :param account_id: The account the row describes.
    :param pool_id: The pool that owns the account.
    :param harness: The harness family the account serves.
    :param active_sessions: Sessions currently running on the account.
    :param sessions: Sessions that used the account within the period.
    :param cost_usd: Cost attributed to those sessions, or ``None`` when
        cost tracking is off.
    :param last_used_at: Unix epoch seconds of the account's last session, or
        ``None``.
    :param quota: The last known quota, when one was observed.
    """

    account_id: str
    pool_id: str
    harness: str
    active_sessions: int = 0
    sessions: int = 0
    cost_usd: float | None = None
    last_used_at: int | None = None
    quota: AccountQuota | None = None
