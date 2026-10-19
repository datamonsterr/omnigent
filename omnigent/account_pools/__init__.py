"""Native account pool support for Omnigent."""

from __future__ import annotations

from omnigent.account_pools.manager import (
    AccountNotFoundError,
    AccountPoolExhaustedError,
    AccountPoolManager,
    AccountReservationTracker,
    ReservationHandle,
    prepare_session_account,
)
from omnigent.account_pools.models import (
    ACCOUNT_ID_LABEL_KEY,
    ACCOUNT_POOL_LABEL_KEY,
    CODEX_HOME_LABEL_KEY,
    GEMINI_DIR_LABEL_KEY,
    AccountConfig,
    AccountPool,
    AccountPoolsConfig,
    parse_account_pools,
)
from omnigent.account_pools.quota import (
    AccountQuota,
    WindowQuota,
    parse_agy_quota,
    parse_codex_rate_limits,
    probe_codex_account_limits,
)

__all__ = [
    "ACCOUNT_ID_LABEL_KEY",
    "ACCOUNT_POOL_LABEL_KEY",
    "CODEX_HOME_LABEL_KEY",
    "GEMINI_DIR_LABEL_KEY",
    "AccountConfig",
    "AccountNotFoundError",
    "AccountPool",
    "AccountPoolExhaustedError",
    "AccountPoolManager",
    "AccountPoolsConfig",
    "AccountQuota",
    "AccountReservationTracker",
    "ReservationHandle",
    "WindowQuota",
    "parse_account_pools",
    "parse_agy_quota",
    "parse_codex_rate_limits",
    "prepare_session_account",
    "probe_codex_account_limits",
]
