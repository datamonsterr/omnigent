"""Tests for native Omnigent account pools and session assignment.

The account inventory lives in the account pool store, so these tests drive
a fake store for the selection rules and the real SQLAlchemy store for
persistence.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from omnigent.account_pools.labels import (
    ACCOUNT_ID_LABEL_KEY,
    ACCOUNT_POOL_LABEL_KEY,
    CODEX_HOME_LABEL_KEY,
    GEMINI_DIR_LABEL_KEY,
)
from omnigent.account_pools.manager import (
    AccountNotFoundError,
    AccountPoolExhaustedError,
    AccountPoolManager,
    prepare_session_account,
    seed_agent_account_label,
)
from omnigent.account_pools.quota import (
    AccountQuota,
    WindowQuota,
    parse_agy_quota,
    parse_codex_rate_limits,
)
from omnigent.entities.account_pool import (
    HOME_KIND_CODEX,
    HOME_KIND_GEMINI,
    AccountPool,
    AccountPoolAccount,
)


class FakeStore:
    """An in-memory account pool store for the selection rules."""

    def __init__(self, pools: list[AccountPool] | None = None) -> None:
        """
        Initialize the fake store.

        :param pools: The pools the store serves.
        """
        self.pools = pools or []
        self.snapshots: dict[str, AccountQuota] = {}

    def get_pool_by_name(self, name: str) -> AccountPool | None:
        """Return a pool by name."""
        for pool in self.pools:
            if pool.name == name:
                return pool
        return None

    def get_pool(self, pool_id: str) -> AccountPool | None:
        """Return a pool by id."""
        for pool in self.pools:
            if pool.id == pool_id:
                return pool
        return None

    def get_pool_for_harness(self, harness: str | None) -> AccountPool | None:
        """Return the pool that serves a harness family."""
        if not harness:
            return None
        normalized = harness.replace("-native", "").replace("-sdk", "").strip()
        for pool in self.pools:
            pool_harness = pool.harness.replace("-native", "").replace("-sdk", "").strip()
            if pool_harness == normalized:
                return pool
        return self.get_pool_by_name("default")

    def get_account(self, account_id: str) -> AccountPoolAccount | None:
        """Return an account by id."""
        for pool in self.pools:
            account = pool.get_account(account_id)
            if account is not None:
                return account
        return None

    def list_pools(self) -> list[AccountPool]:
        """Return every pool."""
        return list(self.pools)

    def save_quota_snapshot(self, account_id: str, quota: AccountQuota) -> None:
        """Record a quota snapshot."""
        self.snapshots[account_id] = quota

    def get_quota_snapshot(
        self, account_id: str, model_family: str | None = None
    ) -> AccountQuota | None:
        """Return a recorded quota snapshot for the account's model family."""
        snapshot = self.snapshots.get(account_id)
        if snapshot is None:
            return None
        if model_family and snapshot.model_family not in (None, model_family):
            return None
        return snapshot

    def list_quota_snapshots(self) -> dict[str, AccountQuota]:
        """Return every recorded quota snapshot."""
        return dict(self.snapshots)


def _account(
    account_id: str,
    home: str | None = None,
    home_kind: str = HOME_KIND_CODEX,
    pool_id: str = "pool",
) -> AccountPoolAccount:
    """Build an account entity for the tests.

    :param account_id: The account id.
    :param home: The credential home path.
    :param home_kind: Which credential home the path is.
    :param pool_id: The pool that owns the account.
    :returns: The account entity.
    """
    return AccountPoolAccount(
        id=account_id,
        pool_id=pool_id,
        home_path=home or "",
        home_kind=home_kind,
    )


def _pool(name: str, accounts: list[AccountPoolAccount]) -> AccountPool:
    """Build a pool entity for the tests.

    :param name: The pool name.
    :param accounts: The pool's accounts.
    :returns: The pool entity.
    """
    pool_id = f"pool-{name}"
    for account in accounts:
        account.pool_id = pool_id
    return AccountPool(id=pool_id, name=name, harness=name, accounts=accounts)


def _manager(
    store: Any,
    quota_provider: Any | None = None,
    conversation_store: Any | None = None,
) -> AccountPoolManager:
    """Build a manager over a store.

    :param store: The account pool store.
    :param quota_provider: The quota probe, or ``None`` for an unknown quota.
    :param conversation_store: The conversation store.
    :returns: The manager.
    """
    provider = quota_provider or (lambda account_id, target: AccountQuota())
    return AccountPoolManager(
        store=store,
        conversation_store=conversation_store,
        quota_provider=provider,
    )


def _known(five_hour: float, weekly: float | None = None) -> AccountQuota:
    """Build a known quota.

    :param five_hour: The remaining five-hour percentage.
    :param weekly: The remaining weekly percentage.
    :returns: The quota.
    """
    return AccountQuota(
        five_hour=WindowQuota(remaining_pct=five_hour, status="known"),
        weekly=WindowQuota(
            remaining_pct=weekly if weekly is not None else five_hour, status="known"
        ),
        status="known",
    )


def test_no_credential_leakage() -> None:
    """A stored account never carries a credential in its annotations."""
    from omnigent.stores.account_pool_store.sqlalchemy_store import (
        SqlAlchemyAccountPoolStore,
        _sanitize_metadata,
    )

    del SqlAlchemyAccountPoolStore
    safe = _sanitize_metadata({"token": "secret-token-xyz", "api_key": "sk-12345", "note": "ok"})
    assert "secret-token-xyz" not in repr(safe)
    assert "sk-12345" not in repr(safe)
    assert safe == {"note": "ok"}


def test_pin_preservation() -> None:
    """An explicit account pin wins over the manager's ranking."""
    store = FakeStore(
        [
            _pool(
                "codex",
                [
                    _account("codex-1", "/tmp/c1"),
                    _account("codex-2", "/tmp/c2"),
                ],
            )
        ]
    )
    mgr = _manager(store)

    labels = {ACCOUNT_ID_LABEL_KEY: "codex-2"}
    res = prepare_session_account(
        initial_labels=labels,
        harness="codex-native",
        sub_agent_name="codex",
        manager=mgr,
    )
    assert res is None
    assert labels[ACCOUNT_ID_LABEL_KEY] == "codex-2"
    assert labels[ACCOUNT_POOL_LABEL_KEY] == "codex"
    assert labels[CODEX_HOME_LABEL_KEY] == str(Path("/tmp/c2"))


def test_pin_preservation_loose_key() -> None:
    """The legacy ``account_id`` label key still pins an account."""
    store = FakeStore([_pool("codex", [_account("codex-1", "/tmp/c1")])])
    mgr = _manager(store)

    labels = {"account_id": "codex-1"}
    prepare_session_account(
        initial_labels=labels,
        harness="codex-native",
        sub_agent_name="codex",
        manager=mgr,
    )
    assert labels[ACCOUNT_ID_LABEL_KEY] == "codex-1"
    assert labels[CODEX_HOME_LABEL_KEY] == str(Path("/tmp/c1"))


@pytest.mark.parametrize("label_key", [ACCOUNT_ID_LABEL_KEY, "account_id"])
@pytest.mark.parametrize(
    "invalid_pin,reason",
    [
        ("missing", "unknown"),
        ("disabled", "disabled"),
        ("agy-1", "harness"),
        ("exhausted", "exhausted"),
        ("blocked", "exhausted"),
    ],
)
def test_explicit_invalid_pin_rejects_without_fallback(
    label_key: str, invalid_pin: str, reason: str
) -> None:
    """A bad explicit pin cannot silently use the healthy account or a default home."""
    disabled = _account("disabled", "/tmp/disabled")
    disabled.enabled = False
    store = FakeStore(
        [
            _pool(
                "codex",
                [disabled, _account("exhausted"), _account("blocked"), _account("healthy")],
            ),
            _pool("antigravity", [_account("agy-1", "/tmp/agy", HOME_KIND_GEMINI)]),
        ]
    )
    store.snapshots = {
        account_id: AccountQuota(status=account_id, observed_at=int(time.time()))
        for account_id in ("exhausted", "blocked")
    }
    labels = {label_key: invalid_pin}
    with pytest.raises(ValueError, match=reason):
        prepare_session_account(labels, "codex-native", None, _manager(store))
    assert labels == {label_key: invalid_pin}


def test_count_first_then_remaining_quota() -> None:
    """Fewest active sessions wins; quota breaks the tie."""
    pool = _pool("codex", [_account("codex-1"), _account("codex-2")])
    quotas = {"codex-1": _known(95.0), "codex-2": _known(30.0)}
    mgr = _manager(FakeStore([pool]), quota_provider=lambda aid, _: quotas[aid])
    mgr.count_active_sessions = MagicMock(side_effect=lambda aid: 1 if aid == "codex-1" else 0)

    chosen, handle = mgr.select_account(pool)
    assert chosen.id == "codex-2"
    mgr.reservation_tracker.release(handle)

    mgr.count_active_sessions = MagicMock(return_value=0)
    chosen, handle = mgr.select_account(pool)
    assert chosen.id == "codex-1"
    mgr.reservation_tracker.release(handle)


def test_known_first_on_same_session_count() -> None:
    """A known quota is preferred over an unknown one."""
    pool = _pool("codex", [_account("codex-1"), _account("codex-2")])
    quotas = {"codex-1": _known(40.0), "codex-2": AccountQuota(status="unknown")}
    mgr = _manager(FakeStore([pool]), quota_provider=lambda aid, _: quotas[aid])
    mgr.count_active_sessions = MagicMock(return_value=0)

    chosen, handle = mgr.select_account(pool)
    assert chosen.id == "codex-1"
    mgr.reservation_tracker.release(handle)


def test_5h_weekly_tie_handling() -> None:
    """Weekly quota breaks a five-hour tie; the id breaks a full tie."""
    pool = _pool("codex", [_account("codex-1"), _account("codex-2")])
    quotas = {"codex-1": _known(80.0, 70.0), "codex-2": _known(80.0, 90.0)}
    mgr = _manager(FakeStore([pool]), quota_provider=lambda aid, _: quotas[aid])
    mgr.count_active_sessions = MagicMock(return_value=0)

    chosen, handle = mgr.select_account(pool)
    assert chosen.id == "codex-2"
    mgr.reservation_tracker.release(handle)

    quotas["codex-2"].weekly.remaining_pct = 70.0
    chosen, handle = mgr.select_account(pool)
    assert chosen.id == "codex-1"
    mgr.reservation_tracker.release(handle)


def test_concurrency_and_pending_reservations() -> None:
    """A pending reservation pushes the next spawn to another account."""
    pool = _pool("codex", [_account("codex-1"), _account("codex-2")])
    mgr = _manager(FakeStore([pool]))
    mgr.count_active_sessions = MagicMock(return_value=0)

    first, handle_one = mgr.select_account(pool)
    assert first.id == "codex-1"

    second, handle_two = mgr.select_account(pool)
    assert second.id == "codex-2"

    mgr.reservation_tracker.release(handle_one)
    mgr.reservation_tracker.release(handle_two)


def test_failure_cleanup() -> None:
    """A released reservation stops counting as pending."""
    pool = _pool("codex", [_account("codex-1")])
    mgr = _manager(FakeStore([pool]))
    mgr.count_active_sessions = MagicMock(return_value=0)

    _chosen, handle = mgr.select_account(pool)
    assert mgr.reservation_tracker.count_pending("codex-1") == 1
    mgr.reservation_tracker.release(handle)
    assert mgr.reservation_tracker.count_pending("codex-1") == 0


def test_exhausted_or_blocked_exclusion() -> None:
    """Exhausted accounts are skipped; an all-exhausted pool raises."""
    pool = _pool("codex", [_account("codex-1"), _account("codex-2")])
    quotas = {
        "codex-1": AccountQuota(
            five_hour=WindowQuota(remaining_pct=0.0, status="exhausted"),
            weekly=WindowQuota(remaining_pct=0.0, status="exhausted"),
            status="blocked",
        ),
        "codex-2": _known(50.0),
    }
    mgr = _manager(FakeStore([pool]), quota_provider=lambda aid, _: quotas[aid])
    mgr.count_active_sessions = MagicMock(return_value=0)

    chosen, handle = mgr.select_account(pool)
    assert chosen.id == "codex-2"
    mgr.reservation_tracker.release(handle)

    quotas["codex-2"].status = "auth_failed"
    with pytest.raises(AccountPoolExhaustedError):
        mgr.select_account(pool)


def test_disabled_accounts_are_not_selected() -> None:
    """A disabled account never wins the selection."""
    disabled = _account("codex-1")
    disabled.enabled = False
    pool = _pool("codex", [disabled, _account("codex-2")])
    mgr = _manager(FakeStore([pool]))
    mgr.count_active_sessions = MagicMock(return_value=0)

    chosen, handle = mgr.select_account(pool)
    assert chosen.id == "codex-2"
    mgr.reservation_tracker.release(handle)


def test_child_creation_uses_chosen_identity_and_forwards_dirs() -> None:
    """Each harness's pool supplies its own credential home."""
    store = FakeStore(
        [
            _pool("codex", [_account("codex-prod", "/data/codex-prod")]),
            _pool(
                "antigravity",
                [_account("agy-prod", "/data/gemini-prod", HOME_KIND_GEMINI)],
            ),
        ]
    )
    mgr = _manager(store)
    mgr.count_active_sessions = MagicMock(return_value=0)

    labels_codex: dict[str, str] = {}
    handle = prepare_session_account(
        initial_labels=labels_codex,
        harness="codex-native",
        sub_agent_name="codex",
        manager=mgr,
    )
    assert handle is not None
    assert labels_codex[ACCOUNT_ID_LABEL_KEY] == "codex-prod"
    assert labels_codex[CODEX_HOME_LABEL_KEY] == str(Path("/data/codex-prod"))
    mgr.reservation_tracker.release(handle)

    labels_agy: dict[str, str] = {}
    handle = prepare_session_account(
        initial_labels=labels_agy,
        harness="antigravity-native",
        sub_agent_name="agy",
        manager=mgr,
    )
    assert handle is not None
    assert labels_agy[ACCOUNT_ID_LABEL_KEY] == "agy-prod"
    assert labels_agy[GEMINI_DIR_LABEL_KEY] == str(Path("/data/gemini-prod"))
    mgr.reservation_tracker.release(handle)


@pytest.mark.parametrize("stale", ["unknown", "disabled"])
def test_stale_saved_default_falls_back_to_pool_pick(
    stale: str, caplog: pytest.LogCaptureFixture
) -> None:
    """A pin to a removed or disabled account launches on the pool's pick."""
    disabled = _account("codex-old", "/data/codex-old")
    disabled.enabled = False
    store = FakeStore([_pool("codex", [disabled, _account("codex-live", "/data/codex-live")])])
    mgr = _manager(store)
    mgr.count_active_sessions = MagicMock(return_value=0)

    pinned = "codex-gone" if stale == "unknown" else "codex-old"
    labels = {ACCOUNT_ID_LABEL_KEY: pinned}
    handle = prepare_session_account(
        initial_labels=labels,
        harness="codex",
        sub_agent_name=None,
        manager=mgr,
        allow_stale_default=True,
    )

    assert handle is not None
    assert labels[ACCOUNT_ID_LABEL_KEY] == "codex-live"
    assert labels[CODEX_HOME_LABEL_KEY] == str(Path("/data/codex-live"))
    assert f"Pinned account {pinned!r} is {stale}" in caplog.text
    mgr.reservation_tracker.release(handle)


def test_stale_saved_default_without_pool_drops_the_pin() -> None:
    """A stale pin with no pool to fall back to leaves the session unassigned."""
    labels = {ACCOUNT_ID_LABEL_KEY: "codex-gone"}
    handle = prepare_session_account(
        initial_labels=labels,
        harness="codex",
        sub_agent_name=None,
        manager=_manager(FakeStore()),
        allow_stale_default=True,
    )
    assert handle is None
    assert labels == {}


@pytest.mark.parametrize("pin", ["missing", "disabled", "wrong-harness"])
def test_explicit_invalid_account_rejects(pin: str) -> None:
    """An explicit account choice must never silently use another identity."""
    disabled = _account("disabled", "/data/disabled")
    disabled.enabled = False
    store = FakeStore(
        [
            _pool("codex", [disabled, _account("live", "/data/live")]),
            _pool("antigravity", [_account("wrong-harness", "/data/gemini", HOME_KIND_GEMINI)]),
        ]
    )
    labels = {ACCOUNT_ID_LABEL_KEY: pin}
    with pytest.raises(AccountNotFoundError):
        prepare_session_account(labels, "codex-native", None, _manager(store))


@pytest.mark.parametrize(
    ("session_labels", "expected"),
    [
        ({}, {ACCOUNT_ID_LABEL_KEY: "codex-2"}),
        ({ACCOUNT_ID_LABEL_KEY: "codex-1"}, {ACCOUNT_ID_LABEL_KEY: "codex-1"}),
        ({"account_id": "codex-1"}, {"account_id": "codex-1"}),
        ({CODEX_HOME_LABEL_KEY: "/data/c1"}, {CODEX_HOME_LABEL_KEY: "/data/c1"}),
    ],
)
def test_agent_default_account_seeds_only_an_unpinned_session(
    session_labels: dict[str, str], expected: dict[str, str]
) -> None:
    """The agent's default account never overrides a session-level choice."""
    spec = MagicMock()
    spec.executor.config = {"harness": "codex", "account_id": " codex-2 "}
    labels = dict(session_labels)
    seed_agent_account_label(labels, spec)
    assert labels == expected


def test_agent_without_default_account_seeds_nothing() -> None:
    """An agent with no ``account_id`` leaves the pick to the pool."""
    spec = MagicMock()
    spec.executor.config = {"harness": "codex", "account_id": ""}
    labels: dict[str, str] = {}
    seed_agent_account_label(labels, spec)
    seed_agent_account_label(labels, None)
    assert labels == {}


def test_parse_codex_rate_limits() -> None:
    """The Codex rate-limits payload maps onto the quota windows."""
    raw = {
        "rateLimits": {
            "ordinaryUsageAllowed": True,
            "primary": {"windowDurationMins": 300, "usedPercent": 15.0, "resetsAt": 1234567},
            "secondary": {"windowDurationMins": 10080, "usedPercent": 40.0, "resetsAt": 2345678},
        }
    }
    quota = parse_codex_rate_limits(raw)
    assert quota.status == "known"
    assert quota.five_hour.remaining_pct == 85.0
    assert quota.weekly.remaining_pct == 60.0
    assert quota.is_exhausted() is False

    raw_blocked = {
        "rateLimits": {
            "ordinaryUsageAllowed": False,
            "rateLimitReachedType": "rate_limit_reached",
            "primary": {"windowDurationMins": 300, "usedPercent": 100.0},
        }
    }
    quota_blocked = parse_codex_rate_limits(raw_blocked)
    assert quota_blocked.status == "exhausted"
    assert quota_blocked.is_exhausted() is True


def test_parse_agy_quota() -> None:
    """The Antigravity quota cache maps onto the quota windows."""
    data = {
        "agy-1": {
            "gemini:five_hour": 75.0,
            "gemini:weekly": 90.0,
            "_status": "known",
        }
    }
    quota = parse_agy_quota(data, "agy-1")
    assert quota.status == "known"
    assert quota.five_hour.remaining_pct == 75.0
    assert quota.weekly.remaining_pct == 90.0
    assert quota.is_exhausted() is False

    data_failed = {"agy-2": {"_status": "auth_failed"}}
    quota_failed = parse_agy_quota(data_failed, "agy-2")
    assert quota_failed.status == "auth_failed"
    assert quota_failed.is_exhausted() is True


def test_count_real_store_active_pending_and_missing_heartbeat(db_uri: str) -> None:
    """Only live sessions count toward an account's load."""
    from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore

    store = SqlAlchemyConversationStore(db_uri)
    now = int(time.time())
    for status, heartbeat in [
        ("running", now),
        ("waiting", now),
        ("running", None),
        ("running", now - 3600),
        ("idle", now),
        (None, None),
    ]:
        conv = store.create_conversation(
            labels={ACCOUNT_ID_LABEL_KEY: "acc-1"}, runner_id=f"r-{status}-{heartbeat}"
        )
        if status is not None:
            store.set_session_live_status(conv.id, status)
        if heartbeat is not None:
            store.touch_runner_liveness([conv.runner_id], now=heartbeat)

    manager = _manager(FakeStore(), conversation_store=store)
    assert manager.count_active_sessions("acc-1") == 3


def test_count_paginates_actual_store(db_uri: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The active-session count walks every page of the conversation store."""
    from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore

    store = SqlAlchemyConversationStore(db_uri)
    for _ in range(5):
        store.create_conversation(labels={ACCOUNT_ID_LABEL_KEY: "acc-1"})
    original = store.list_conversations

    def small_pages(**kwargs: Any) -> Any:
        kwargs["limit"] = 2
        return original(**kwargs)

    monkeypatch.setattr(store, "list_conversations", small_pages)
    manager = _manager(FakeStore(), conversation_store=store)
    assert manager.count_active_sessions("acc-1") == 5


def test_store_failures_do_not_become_zero_sessions() -> None:
    """A store failure propagates instead of reading as an idle account."""
    store = MagicMock()
    store.list_conversations.side_effect = RuntimeError("database unavailable")
    manager = _manager(FakeStore(), conversation_store=store)
    with pytest.raises(RuntimeError, match="database unavailable"):
        manager.count_active_sessions("acc-1")


def test_concurrent_selection_reserves_atomically() -> None:
    """Two concurrent selections take different accounts."""
    from concurrent.futures import ThreadPoolExecutor

    pool = _pool("codex", [_account("a"), _account("b")])
    mgr = _manager(FakeStore([pool]))
    count_pending = mgr.reservation_tracker.count_pending

    def delayed_count(account_id: str) -> int:
        result = count_pending(account_id)
        time.sleep(0.01)
        return result

    mgr.reservation_tracker.count_pending = delayed_count
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: mgr.select_account(pool)[0].id, range(2)))
    assert sorted(results) == ["a", "b"]


@pytest.mark.parametrize(
    "key,home_kind,harness",
    [
        (CODEX_HOME_LABEL_KEY, HOME_KIND_CODEX, "codex-native"),
        (GEMINI_DIR_LABEL_KEY, HOME_KIND_GEMINI, "antigravity-native"),
    ],
)
def test_explicit_home_pins_identity_without_account_id(
    key: str, home_kind: str, harness: str
) -> None:
    """A pinned credential home identifies the account it belongs to."""
    accounts = [
        _account("a", "/tmp/account-a", home_kind),
        _account("b", "/tmp/account-b", home_kind),
    ]
    pool = _pool(harness, accounts)
    manager = _manager(FakeStore([pool]))

    labels = {key: "/tmp/account-b"}
    assert prepare_session_account(labels, harness, None, manager) is None
    assert labels[ACCOUNT_ID_LABEL_KEY] == "b"

    labels = {key: "/tmp/unlisted-home"}
    assert prepare_session_account(labels, harness, None, manager) is None
    assert ACCOUNT_ID_LABEL_KEY not in labels

    labels = {key: "/tmp/account-b", ACCOUNT_ID_LABEL_KEY: "a"}
    with pytest.raises(ValueError, match="conflicts"):
        prepare_session_account(labels, harness, None, manager)


def test_existing_home_only_sessions_are_counted(db_uri: str) -> None:
    """A session that only carries a credential home still counts."""
    from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore

    account = _account("a", "/tmp/account-a")
    pool = _pool("codex", [account])
    store = SqlAlchemyConversationStore(db_uri)
    manager = _manager(FakeStore([pool]), conversation_store=store)
    store.create_conversation(labels={CODEX_HOME_LABEL_KEY: "/tmp/account-a"})
    assert manager.count_active_sessions("a") == 1


def test_agy_uses_selected_model_family_and_ignores_stale_cache(tmp_path: Path) -> None:
    """The Antigravity probe reads the model family and honours freshness."""
    import json

    from omnigent.account_pools.quota import (
        clear_quota_cache,
        get_default_quota_provider,
        probe_agy_quota,
    )

    path = tmp_path / "quota.json"
    path.write_text(
        json.dumps(
            {"ts": time.time(), "accounts": {"a": {"gemini:five_hour": 80, "claude:five_hour": 0}}}
        )
    )
    account = _account("a", str(tmp_path), HOME_KIND_GEMINI)
    clear_quota_cache()
    manager = _manager(FakeStore(), quota_provider=get_default_quota_provider())
    assert manager.get_quota(account, "gemini-flash").five_hour.remaining_pct == 80
    assert manager.get_quota(account, "claude-sonnet").is_exhausted()
    path.write_text(
        json.dumps({"ts": time.time() - 3600, "accounts": {"a": {"gemini:five_hour": 0}}})
    )
    assert not probe_agy_quota(tmp_path, "a").is_known()
    assert not probe_agy_quota(tmp_path, "a").is_exhausted()


def test_quota_cache_does_not_mix_same_id_different_homes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two homes sharing an account id keep separate quota entries."""
    from omnigent.account_pools import quota

    quota.clear_quota_cache()
    monkeypatch.setattr(
        quota,
        "probe_codex_account_limits",
        lambda *args, **kw: AccountQuota(
            five_hour=WindowQuota(remaining_pct=10 if kw["codex_home"].endswith("a") else 90),
            status="known",
        ),
    )
    provider = quota.get_default_quota_provider()
    first = quota.quota_target_for(_account("same", "/tmp/a"))
    second = quota.quota_target_for(_account("same", "/tmp/b"))
    assert provider("same", first).five_hour.remaining_pct == 10
    assert provider("same", second).five_hour.remaining_pct == 90


def test_quota_first_prioritizes_fresh_remaining_then_balances_ties() -> None:
    """An opt-in Codex pool favors fresh quota over load and balances equal quotas."""
    pool = _pool("codex", [_account("a"), _account("b"), _account("unknown")])
    quotas = {"a": _known(90, 40), "b": _known(80, 90), "unknown": AccountQuota()}
    manager = AccountPoolManager(
        FakeStore([pool]),
        quota_provider=lambda aid, _: quotas[aid],
        codex_selection_policy="quota_first",
    )
    manager.count_active_sessions = MagicMock(side_effect=lambda aid: 10 if aid == "a" else 0)
    first, handle = manager.select_account(pool)
    assert first.id == "a"
    manager.reservation_tracker.release(handle)
    quotas["b"].five_hour.remaining_pct = 90
    second, handle = manager.select_account(pool)
    assert second.id == "b"
    manager.reservation_tracker.release(handle)
    quotas["b"].weekly.remaining_pct = 40
    manager.count_active_sessions = MagicMock(return_value=0)
    first, first_handle = manager.select_account(pool)
    second, second_handle = manager.select_account(pool)
    assert [first.id, second.id] == ["a", "b"]
    manager.reservation_tracker.release(first_handle)
    manager.reservation_tracker.release(second_handle)
    labels = {ACCOUNT_ID_LABEL_KEY: "unknown"}
    prepare_session_account(
        initial_labels=labels, harness="codex", sub_agent_name=None, manager=manager
    )
    assert labels[ACCOUNT_ID_LABEL_KEY] == "unknown"


@pytest.mark.parametrize("status", ["exhausted", "blocked", "auth_failed"])
@pytest.mark.parametrize("failure", ["raises", "unknown", "probe_error", "stale_known"])
def test_quota_first_retains_unavailable_evidence_until_fresh_recovery(
    status: str, failure: str
) -> None:
    """A failed or stale probe cannot rehabilitate an unavailable account."""
    pool = _pool("codex", [_account("a")])
    store = FakeStore([pool])
    stored = AccountQuota(status=status, observed_at=int(time.time()) - 121)
    store.save_quota_snapshot("a", stored)

    def probe(_aid, _target):
        if failure == "raises":
            raise RuntimeError("probe unavailable")
        if failure == "stale_known":
            result = _known(100)
            result.observed_at = int(time.time()) - 121
            return result
        return AccountQuota(status=failure)

    manager = AccountPoolManager(store, quota_provider=probe, codex_selection_policy="quota_first")
    for _ in range(2):
        with pytest.raises(AccountPoolExhaustedError):
            manager.select_account(pool)
    assert store.get_quota_snapshot("a").status == status
    manager.quota_provider = lambda _aid, _target: _known(20)
    chosen, handle = manager.select_account(pool)
    assert chosen.id == "a"
    manager.reservation_tracker.release(handle)


@pytest.mark.parametrize("offset", [-121, 121])
def test_quota_first_does_not_rank_stale_or_future_quota_as_known(offset: int) -> None:
    """Neither expired nor future observations establish available quota."""
    pool = _pool("codex", [_account("a"), _account("b")])

    def probe(aid, _target):
        quota = _known(100 if aid == "a" else 20)
        quota.observed_at = int(time.time()) + offset if aid == "a" else int(time.time())
        return quota

    manager = AccountPoolManager(
        FakeStore([pool]), quota_provider=probe, codex_selection_policy="quota_first"
    )
    chosen, handle = manager.select_account(pool)
    assert chosen.id == "b"
    manager.reservation_tracker.release(handle)


def test_quota_first_applies_only_to_codex_and_rejects_invalid_configuration() -> None:
    """Other harnesses retain load-first ranking; policy typos fail closed."""
    pool = _pool("antigravity-native", [_account("a"), _account("b")])
    manager = AccountPoolManager(
        FakeStore([pool]),
        quota_provider=lambda aid, _: _known(90 if aid == "a" else 20),
        codex_selection_policy="quota_first",
    )
    manager.count_active_sessions = MagicMock(side_effect=lambda aid: 10 if aid == "a" else 0)
    chosen, handle = manager.select_account(pool)
    assert chosen.id == "b"
    manager.reservation_tracker.release(handle)
    with pytest.raises(ValueError, match="codex_selection_policy"):
        AccountPoolManager(FakeStore(), codex_selection_policy="quota_frist")
