"""Native account pool support for Omnigent.

An account pool groups the subscription accounts that serve one harness
family. The inventory lives in the account pool store (database-backed), the
selection logic lives in :mod:`omnigent.account_pools.manager`, and the
credential homes are read by the runner from the session labels.
"""

from __future__ import annotations

from omnigent.account_pools.labels import (
    ACCOUNT_HOME_LABEL_KEYS,
    ACCOUNT_ID_LABEL_KEY,
    ACCOUNT_POOL_LABEL_KEY,
    CODEX_HOME_LABEL_KEY,
    GEMINI_DIR_LABEL_KEY,
    LEGACY_ACCOUNT_ID_LABEL_KEY,
)
from omnigent.account_pools.manager import (
    AccountNotFoundError,
    AccountPoolExhaustedError,
    AccountPoolManager,
    AccountReservationTracker,
    ReservationHandle,
    prepare_session_account,
)
from omnigent.account_pools.quota import (
    AccountQuota,
    QuotaTarget,
    WindowQuota,
    get_default_quota_provider,
    parse_agy_quota,
    parse_codex_rate_limits,
    probe_agy_quota,
    probe_codex_account_limits,
    quota_target_for,
)
from omnigent.entities.account_pool import (
    AccountPool,
    AccountPoolAccount,
    AccountQuotaWindow,
    AccountUsage,
)

__all__ = [
    "ACCOUNT_HOME_LABEL_KEYS",
    "ACCOUNT_ID_LABEL_KEY",
    "ACCOUNT_POOL_LABEL_KEY",
    "CODEX_HOME_LABEL_KEY",
    "GEMINI_DIR_LABEL_KEY",
    "LEGACY_ACCOUNT_ID_LABEL_KEY",
    "AccountNotFoundError",
    "AccountPool",
    "AccountPoolAccount",
    "AccountPoolExhaustedError",
    "AccountPoolManager",
    "AccountQuota",
    "AccountQuotaWindow",
    "AccountReservationTracker",
    "AccountUsage",
    "QuotaTarget",
    "ReservationHandle",
    "WindowQuota",
    "get_default_quota_provider",
    "parse_agy_quota",
    "parse_codex_rate_limits",
    "prepare_session_account",
    "probe_agy_quota",
    "probe_codex_account_limits",
    "quota_target_for",
]
