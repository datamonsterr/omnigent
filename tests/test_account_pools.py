"""Tests for native Omnigent account pools and session assignment."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from omnigent.account_pools.manager import (
    AccountPoolExhaustedError,
    AccountPoolManager,
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
)
from omnigent.account_pools.quota import (
    AccountQuota,
    WindowQuota,
    parse_agy_quota,
    parse_codex_rate_limits,
)


def _make_pool(accounts: list[AccountConfig], name: str = "codex") -> AccountPool:
    return AccountPool(name=name, accounts=accounts)


def test_account_config_validation():
    # Valid
    acc = AccountConfig(id="codex-1", codex_home="/tmp/codex-1")
    assert acc.id == "codex-1"
    assert acc.codex_home == Path("/tmp/codex-1").expanduser()

    # Invalid empty or non-string id
    with pytest.raises(ValueError, match="non-empty string"):
        AccountConfig(id="")

    # Invalid chars
    with pytest.raises(ValueError, match="Invalid account id"):
        AccountConfig(id="codex 1/bad")

    # Invalid path types
    with pytest.raises(ValueError, match="string or Path"):
        AccountConfig(id="codex-2", codex_home=123)  # type: ignore


def test_no_credential_leakage():
    acc = AccountConfig(
        id="codex-1",
        codex_home=Path("/tmp/c1"),
        metadata={"token": "secret-token-xyz", "api_key": "sk-12345"},
    )
    rep = repr(acc)
    assert "secret-token-xyz" not in rep
    assert "sk-12345" not in rep
    assert "codex-1" in rep


def test_pin_preservation():
    acc1 = AccountConfig(id="codex-1", codex_home=Path("/tmp/c1"))
    acc2 = AccountConfig(id="codex-2", codex_home=Path("/tmp/c2"))
    pool = _make_pool([acc1, acc2], name="codex")
    cfg = AccountPoolsConfig(pools={"codex": pool})
    mgr = AccountPoolManager(config=cfg)

    # When explicit pin is provided, it must be honored regardless of session counts or quota
    initial_labels = {ACCOUNT_ID_LABEL_KEY: "codex-2"}
    res = prepare_session_account(
        initial_labels=initial_labels,
        harness="codex-native",
        sub_agent_name="codex",
        agent_spec=None,
        manager=mgr,
        conversation_store=None,
    )
    assert res is None  # no reservation for pinned
    assert initial_labels[ACCOUNT_ID_LABEL_KEY] == "codex-2"
    assert initial_labels[ACCOUNT_POOL_LABEL_KEY] == "codex"
    assert initial_labels[CODEX_HOME_LABEL_KEY] == str(Path("/tmp/c2").expanduser())


def test_pin_preservation_loose_key():
    acc1 = AccountConfig(id="codex-1", codex_home=Path("/tmp/c1"))
    pool = _make_pool([acc1], name="codex")
    cfg = AccountPoolsConfig(pools={"codex": pool})
    mgr = AccountPoolManager(config=cfg)

    initial_labels = {"account_id": "codex-1"}
    prepare_session_account(
        initial_labels=initial_labels,
        harness="codex-native",
        sub_agent_name="codex",
        agent_spec=None,
        manager=mgr,
        conversation_store=None,
    )
    assert initial_labels[ACCOUNT_ID_LABEL_KEY] == "codex-1"
    assert initial_labels[CODEX_HOME_LABEL_KEY] == str(Path("/tmp/c1").expanduser())


def test_count_first_then_remaining_quota():
    acc1 = AccountConfig(id="codex-1")
    acc2 = AccountConfig(id="codex-2")
    pool = _make_pool([acc1, acc2], name="codex")

    quotas = {
        "codex-1": AccountQuota(
            five_hour=WindowQuota(remaining_pct=95.0, status="known"),
            weekly=WindowQuota(remaining_pct=95.0, status="known"),
            status="known",
        ),
        "codex-2": AccountQuota(
            five_hour=WindowQuota(remaining_pct=30.0, status="known"),
            weekly=WindowQuota(remaining_pct=30.0, status="known"),
            status="known",
        ),
    }

    mgr = AccountPoolManager(
        config=AccountPoolsConfig(pools={"codex": pool}),
        quota_provider=lambda aid, _: quotas[aid],
    )

    # Mock count_active_sessions: codex-1 has 1 active session, codex-2 has 0 active sessions
    mgr.count_active_sessions = MagicMock(side_effect=lambda _, aid: 1 if aid == "codex-1" else 0)

    # Count first: codex-2 should be selected despite lower quota
    chosen, handle = mgr.select_account(pool)
    assert chosen.id == "codex-2"
    mgr.reservation_tracker.release(handle)

    # When session counts are equal, most remaining quota wins
    mgr.count_active_sessions = MagicMock(return_value=0)
    chosen, handle = mgr.select_account(pool)
    assert chosen.id == "codex-1"  # 95% > 30%
    mgr.reservation_tracker.release(handle)


def test_known_first_on_same_session_count():
    acc1 = AccountConfig(id="codex-1")
    acc2 = AccountConfig(id="codex-2")
    pool = _make_pool([acc1, acc2], name="codex")

    # codex-1 has known quota (40%), codex-2 has unknown quota
    quotas = {
        "codex-1": AccountQuota(
            five_hour=WindowQuota(remaining_pct=40.0, status="known"),
            weekly=WindowQuota(remaining_pct=40.0, status="known"),
            status="known",
        ),
        "codex-2": AccountQuota(
            five_hour=WindowQuota(status="unknown"),
            weekly=WindowQuota(status="unknown"),
            status="unknown",
        ),
    }

    mgr = AccountPoolManager(
        config=AccountPoolsConfig(pools={"codex": pool}),
        quota_provider=lambda aid, _: quotas[aid],
    )
    mgr.count_active_sessions = MagicMock(return_value=0)

    # On same session count, known quota is preferred over unknown
    chosen, handle = mgr.select_account(pool)
    assert chosen.id == "codex-1"
    mgr.reservation_tracker.release(handle)


def test_5h_weekly_tie_handling():
    acc1 = AccountConfig(id="codex-1")
    acc2 = AccountConfig(id="codex-2")
    pool = _make_pool([acc1, acc2], name="codex")

    # Both have 0 active sessions and identical 5h quota (80%),
    # but acc2 has higher weekly quota (90% vs 70%)
    quotas = {
        "codex-1": AccountQuota(
            five_hour=WindowQuota(remaining_pct=80.0, status="known"),
            weekly=WindowQuota(remaining_pct=70.0, status="known"),
            status="known",
        ),
        "codex-2": AccountQuota(
            five_hour=WindowQuota(remaining_pct=80.0, status="known"),
            weekly=WindowQuota(remaining_pct=90.0, status="known"),
            status="known",
        ),
    }

    mgr = AccountPoolManager(
        config=AccountPoolsConfig(pools={"codex": pool}),
        quota_provider=lambda aid, _: quotas[aid],
    )
    mgr.count_active_sessions = MagicMock(return_value=0)

    chosen, handle = mgr.select_account(pool)
    assert chosen.id == "codex-2"
    mgr.reservation_tracker.release(handle)

    # Stable ID tie-break when both 5h and weekly quota are identical
    quotas["codex-2"].weekly.remaining_pct = 70.0
    chosen, handle = mgr.select_account(pool)
    assert chosen.id == "codex-1"  # "codex-1" < "codex-2"
    mgr.reservation_tracker.release(handle)


def test_concurrency_and_pending_reservations():
    acc1 = AccountConfig(id="codex-1")
    acc2 = AccountConfig(id="codex-2")
    pool = _make_pool([acc1, acc2], name="codex")

    mgr = AccountPoolManager(config=AccountPoolsConfig(pools={"codex": pool}))
    mgr.count_active_sessions = MagicMock(return_value=0)

    # First spawn selects codex-1 and acquires pending reservation
    c1, h1 = mgr.select_account(pool)
    assert c1.id == "codex-1"

    # Second spawn without DB update sees codex-1 has 1 pending reservation, so selects codex-2!
    c2, h2 = mgr.select_account(pool)
    assert c2.id == "codex-2"

    mgr.reservation_tracker.release(h1)
    mgr.reservation_tracker.release(h2)


def test_failure_cleanup():
    acc1 = AccountConfig(id="codex-1")
    pool = _make_pool([acc1], name="codex")
    mgr = AccountPoolManager(config=AccountPoolsConfig(pools={"codex": pool}))
    mgr.count_active_sessions = MagicMock(return_value=0)

    # Acquire reservation
    _c1, h1 = mgr.select_account(pool)
    assert mgr.reservation_tracker.count_pending("codex-1") == 1

    # Release on failure
    mgr.reservation_tracker.release(h1)
    assert mgr.reservation_tracker.count_pending("codex-1") == 0


def test_exhausted_or_blocked_exclusion():
    acc1 = AccountConfig(id="codex-1")
    acc2 = AccountConfig(id="codex-2")
    pool = _make_pool([acc1, acc2], name="codex")

    quotas = {
        "codex-1": AccountQuota(
            five_hour=WindowQuota(remaining_pct=0.0, status="exhausted"),
            weekly=WindowQuota(remaining_pct=0.0, status="exhausted"),
            status="blocked",
        ),
        "codex-2": AccountQuota(
            five_hour=WindowQuota(remaining_pct=50.0, status="known"),
            weekly=WindowQuota(remaining_pct=50.0, status="known"),
            status="known",
        ),
    }

    mgr = AccountPoolManager(
        config=AccountPoolsConfig(pools={"codex": pool}),
        quota_provider=lambda aid, _: quotas[aid],
    )
    mgr.count_active_sessions = MagicMock(return_value=0)

    chosen, handle = mgr.select_account(pool)
    assert chosen.id == "codex-2"
    mgr.reservation_tracker.release(handle)

    # If all accounts are exhausted, raises AccountPoolExhaustedError
    quotas["codex-2"].status = "auth_failed"
    with pytest.raises(AccountPoolExhaustedError):
        mgr.select_account(pool)


def test_child_creation_uses_chosen_identity_and_forwards_dirs():
    acc_codex = AccountConfig(id="codex-prod", codex_home=Path("/data/codex-prod"))
    acc_agy = AccountConfig(id="agy-prod", gemini_dir=Path("/data/gemini-prod"))
    pool_codex = _make_pool([acc_codex], name="codex")
    pool_agy = _make_pool([acc_agy], name="antigravity")

    cfg = AccountPoolsConfig(pools={"codex": pool_codex, "antigravity": pool_agy})
    mgr = AccountPoolManager(config=cfg)
    mgr.count_active_sessions = MagicMock(return_value=0)

    # Child codex dispatch
    labels_codex: dict[str, str] = {}
    h1 = prepare_session_account(
        initial_labels=labels_codex,
        harness="codex-native",
        sub_agent_name="codex",
        agent_spec=None,
        manager=mgr,
        conversation_store=None,
    )
    assert h1 is not None
    assert labels_codex[ACCOUNT_ID_LABEL_KEY] == "codex-prod"
    assert labels_codex[CODEX_HOME_LABEL_KEY] == str(Path("/data/codex-prod").expanduser())
    mgr.reservation_tracker.release(h1)

    # Child antigravity dispatch
    labels_agy: dict[str, str] = {}
    h2 = prepare_session_account(
        initial_labels=labels_agy,
        harness="antigravity-native",
        sub_agent_name="agy",
        agent_spec=None,
        manager=mgr,
        conversation_store=None,
    )
    assert h2 is not None
    assert labels_agy[ACCOUNT_ID_LABEL_KEY] == "agy-prod"
    assert labels_agy[GEMINI_DIR_LABEL_KEY] == str(Path("/data/gemini-prod").expanduser())
    mgr.reservation_tracker.release(h2)


def test_parse_codex_rate_limits():
    # Valid rate limits response
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

    # Blocked
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


def test_parse_agy_quota():
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

    # Auth failed
    data_failed = {"agy-2": {"_status": "auth_failed"}}
    quota_failed = parse_agy_quota(data_failed, "agy-2")
    assert quota_failed.status == "auth_failed"
    assert quota_failed.is_exhausted() is True


def test_parse_account_pools_declarative():
    from omnigent.account_pools.models import parse_account_pools

    raw = {
        "codex": [
            {"id": "codex-1", "codex_home": "~/.codex-1"},
            {"id": "codex-2", "codex_home": "/tmp/codex-2"},
        ],
        "antigravity": {
            "accounts": [
                {"id": "agy-1", "gemini_dir": "~/.gemini-1"},
            ]
        },
        "opencode": ["opencode-1", "opencode-2"],
    }
    cfg = parse_account_pools(raw)
    assert cfg is not None
    assert "codex" in cfg.pools
    assert len(cfg.pools["codex"].accounts) == 2
    assert cfg.pools["codex"].accounts[0].id == "codex-1"
    assert "antigravity" in cfg.pools
    assert (
        cfg.pools["antigravity"].accounts[0].gemini_dir
        == Path("~/.gemini-1").expanduser().resolve()
    )
    assert "opencode" in cfg.pools
    assert len(cfg.pools["opencode"].accounts) == 2

    # None / empty handling
    assert parse_account_pools(None) is None
    assert parse_account_pools({}) is None
    assert parse_account_pools("bad") is None


def test_count_real_store_active_pending_and_missing_heartbeat(db_uri):
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
    assert AccountPoolManager().count_active_sessions(store, "acc-1") == 3


def test_count_paginates_actual_store(db_uri, monkeypatch):
    from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore

    store = SqlAlchemyConversationStore(db_uri)
    for _ in range(5):
        store.create_conversation(labels={ACCOUNT_ID_LABEL_KEY: "acc-1"})
    original = store.list_conversations

    def small_pages(**kwargs):
        kwargs["limit"] = 2
        return original(**kwargs)

    monkeypatch.setattr(store, "list_conversations", small_pages)
    assert AccountPoolManager().count_active_sessions(store, "acc-1") == 5


def test_store_failures_do_not_become_zero_sessions():
    store = MagicMock()
    store.list_conversations.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError, match="database unavailable"):
        AccountPoolManager().count_active_sessions(store, "acc-1")


def test_concurrent_selection_reserves_atomically():
    from concurrent.futures import ThreadPoolExecutor

    pool = _make_pool([AccountConfig(id="a"), AccountConfig(id="b")])
    mgr = AccountPoolManager(quota_provider=lambda *_: AccountQuota())
    # Yield after reading pending counts, when an unlocked selector can race.
    count_pending = mgr.reservation_tracker.count_pending

    def delayed_count(account_id):
        result = count_pending(account_id)
        time.sleep(0.01)
        return result

    mgr.reservation_tracker.count_pending = delayed_count
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: mgr.select_account(pool)[0].id, range(2)))
    assert sorted(results) == ["a", "b"]


@pytest.mark.parametrize(
    "key,home,harness",
    [
        (CODEX_HOME_LABEL_KEY, "codex_home", "codex-native"),
        (GEMINI_DIR_LABEL_KEY, "gemini_dir", "antigravity-native"),
    ],
)
def test_explicit_home_pins_identity_without_account_id(key, home, harness):
    accounts = [
        AccountConfig(id="a", **{home: "/tmp/account-a"}),
        AccountConfig(id="b", **{home: "/tmp/account-b"}),
    ]
    pool = _make_pool(accounts, name=harness)
    manager = AccountPoolManager(config=AccountPoolsConfig(pools={harness: pool}))
    labels = {key: "/tmp/account-b"}
    assert prepare_session_account(labels, harness, None, None, manager) is None
    assert labels[ACCOUNT_ID_LABEL_KEY] == "b"
    labels = {key: "/tmp/unlisted-home"}
    assert prepare_session_account(labels, harness, None, None, manager) is None
    assert ACCOUNT_ID_LABEL_KEY not in labels
    labels = {key: "/tmp/account-b", ACCOUNT_ID_LABEL_KEY: "a"}
    with pytest.raises(ValueError, match="conflicts"):
        prepare_session_account(labels, harness, None, None, manager)


def test_agent_spec_declared_pools():
    # When agent_spec has executor.config.account_pools, it takes effect
    class FakeExecutor:
        def __init__(self, cfg):
            self.config = cfg

    class FakeSpec:
        def __init__(self, exec_cfg):
            self.executor = FakeExecutor(exec_cfg)

    spec = FakeSpec(
        {
            "account_pools": {
                "codex": [
                    {"id": "spec-codex-1", "codex_home": "/tmp/spec-c1"},
                    {"id": "spec-codex-2", "codex_home": "/tmp/spec-c2"},
                ]
            }
        }
    )

    labels: dict[str, str] = {}
    mgr = AccountPoolManager()  # empty global manager
    mgr.count_active_sessions = MagicMock(return_value=0)

    handle = prepare_session_account(
        initial_labels=labels,
        harness="codex-native",
        sub_agent_name="codex",
        agent_spec=spec,
        manager=mgr,
        conversation_store=None,
    )
    assert handle is not None
    assert labels[ACCOUNT_ID_LABEL_KEY] == "spec-codex-1"
    assert labels[CODEX_HOME_LABEL_KEY] == str(Path("/tmp/spec-c1").resolve())
    mgr.reservation_tracker.release(handle)


def test_existing_home_only_sessions_are_counted(db_uri):
    from omnigent.stores.conversation_store.sqlalchemy_store import SqlAlchemyConversationStore

    account = AccountConfig(id="a", codex_home="/tmp/account-a")
    manager = AccountPoolManager(config=AccountPoolsConfig(pools={"codex": _make_pool([account])}))
    store = SqlAlchemyConversationStore(db_uri)
    store.create_conversation(labels={CODEX_HOME_LABEL_KEY: "/tmp/account-a"})
    assert manager.count_active_sessions(store, "a") == 1


def test_agy_uses_selected_model_family_and_ignores_stale_cache(tmp_path):
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
    account = AccountConfig(id="a", gemini_dir=tmp_path)
    clear_quota_cache()
    manager = AccountPoolManager(quota_provider=get_default_quota_provider())
    assert manager.get_quota(account, "gemini-flash").five_hour.remaining_pct == 80
    assert manager.get_quota(account, "claude-sonnet").is_exhausted()
    path.write_text(
        json.dumps({"ts": time.time() - 3600, "accounts": {"a": {"gemini:five_hour": 0}}})
    )
    assert not probe_agy_quota(tmp_path, "a").is_known()
    assert not probe_agy_quota(tmp_path, "a").is_exhausted()


def test_quota_cache_does_not_mix_same_id_different_homes(monkeypatch):
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
    assert (
        provider("same", AccountConfig(id="same", codex_home="/tmp/a")).five_hour.remaining_pct
        == 10
    )
    assert (
        provider("same", AccountConfig(id="same", codex_home="/tmp/b")).five_hour.remaining_pct
        == 90
    )
