"""Integration tests for native account pool selection in session creation.

The account inventory lives in the account pool store, so these tests seed
the store and let the server pick the account on the session-create path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from omnigent.account_pools.labels import (
    ACCOUNT_ID_LABEL_KEY,
    ACCOUNT_POOL_LABEL_KEY,
    CODEX_HOME_LABEL_KEY,
    GEMINI_DIR_LABEL_KEY,
)
from omnigent.account_pools.manager import AccountPoolManager
from omnigent.account_pools.quota import AccountQuota
from omnigent.entities.account_pool import HOME_KIND_CODEX, HOME_KIND_GEMINI
from omnigent.stores.account_pool_store.sqlalchemy_store import SqlAlchemyAccountPoolStore
from tests.server.helpers import create_test_agent


@pytest.fixture
def account_pool_store(client: AsyncClient) -> SqlAlchemyAccountPoolStore:
    """Seed the account inventory the server reads.

    :param client: The HTTP client, used to reach the app state.
    :returns: The seeded account pool store.
    """
    store: SqlAlchemyAccountPoolStore = client._transport.app.state.account_pool_store
    codex = store.create_pool("pool-codex", "codex", "codex-native")
    store.create_account("codex-1", codex.id, "/var/accounts/codex-1", home_kind=HOME_KIND_CODEX)
    store.create_account("codex-2", codex.id, "/var/accounts/codex-2", home_kind=HOME_KIND_CODEX)
    agy = store.create_pool("pool-antigravity", "antigravity", "antigravity-native")
    store.create_account(
        "agy-1", agy.id, "/var/accounts/agy-1/.gemini", home_kind=HOME_KIND_GEMINI
    )
    store.create_account(
        "agy-2", agy.id, "/var/accounts/agy-2/.gemini", home_kind=HOME_KIND_GEMINI
    )
    return store


def _install_manager(
    client: AsyncClient,
    store: SqlAlchemyAccountPoolStore,
    quota_provider: object | None = None,
) -> AccountPoolManager:
    """Install a manager over the seeded store.

    :param client: The HTTP client, used to reach the app state.
    :param store: The account pool store.
    :param quota_provider: An optional quota probe override.
    :returns: The installed manager.
    """
    app = client._transport.app
    if quota_provider is None:
        # The default provider probes the real Codex CLI; against a test home
        # path its answer is timing-dependent (an auth failure reads as an
        # exhausted account). A fixed unknown quota keeps the selection
        # deterministic.
        quota_provider = lambda _aid, _target: AccountQuota()  # noqa: E731
    manager = AccountPoolManager(
        store=store,
        conversation_store=app.state.conversation_store,
        quota_provider=quota_provider,  # type: ignore[arg-type]
    )
    app.state.account_pool_manager = manager
    return manager


@pytest.mark.asyncio
async def test_session_create_picks_account_and_forwards_env(
    client: AsyncClient,
    account_pool_store: SqlAlchemyAccountPoolStore,
) -> None:
    """A session create picks the least loaded account and forwards its home."""
    _install_manager(client, account_pool_store)

    agent = await create_test_agent(client)
    agent_id = agent["id"]

    resp1 = await client.post(
        "/v1/sessions",
        json={
            "agent_id": agent_id,
            "title": "Codex Task 1",
            "harness_override": "codex-native",
        },
    )
    assert resp1.status_code == 201
    labels1 = resp1.json().get("labels", {})
    assert labels1.get(ACCOUNT_ID_LABEL_KEY) == "codex-1"
    assert labels1.get(ACCOUNT_POOL_LABEL_KEY) == "codex"
    assert labels1.get(CODEX_HOME_LABEL_KEY) == str(Path("/var/accounts/codex-1").resolve())

    resp2 = await client.post(
        "/v1/sessions",
        json={
            "agent_id": agent_id,
            "title": "Codex Task 2",
            "harness_override": "codex-native",
        },
    )
    assert resp2.status_code == 201
    labels2 = resp2.json().get("labels", {})
    assert labels2.get(ACCOUNT_ID_LABEL_KEY) == "codex-2"
    assert labels2.get(CODEX_HOME_LABEL_KEY) == str(Path("/var/accounts/codex-2").resolve())

    resp3 = await client.post(
        "/v1/sessions",
        json={
            "agent_id": agent_id,
            "title": "Codex Pinned",
            "harness_override": "codex-native",
            "labels": {ACCOUNT_ID_LABEL_KEY: "codex-1"},
        },
    )
    assert resp3.status_code == 201
    labels3 = resp3.json().get("labels", {})
    assert labels3.get(ACCOUNT_ID_LABEL_KEY) == "codex-1"
    assert labels3.get(CODEX_HOME_LABEL_KEY) == str(Path("/var/accounts/codex-1").resolve())


@pytest.mark.asyncio
async def test_session_create_antigravity_forwards_gemini_dir(
    client: AsyncClient,
    account_pool_store: SqlAlchemyAccountPoolStore,
) -> None:
    """The Antigravity pool forwards its gemini dir."""
    _install_manager(client, account_pool_store)

    agent = await create_test_agent(client)
    resp = await client.post(
        "/v1/sessions",
        json={
            "agent_id": agent["id"],
            "title": "Agy Task",
            "harness_override": "antigravity-native",
        },
    )
    assert resp.status_code == 201
    labels = resp.json().get("labels", {})
    assert labels.get(ACCOUNT_ID_LABEL_KEY) == "agy-1"
    assert labels.get(ACCOUNT_POOL_LABEL_KEY) == "antigravity"
    assert labels.get(GEMINI_DIR_LABEL_KEY) == str(Path("/var/accounts/agy-1/.gemini").resolve())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "named_child,home_source", [(False, "config"), (True, "config"), (False, "labels")]
)
async def test_explicit_account_overrides_spec_home_default(
    client: AsyncClient,
    account_pool_store: SqlAlchemyAccountPoolStore,
    named_child: bool,
    home_source: str,
) -> None:
    """A per-create account overrides the registered agent's or named worker's home."""
    _install_manager(client, account_pool_store)
    executor: dict[str, Any] = {"type": "omnigent", "config": {"harness": "codex-native"}}
    guardrails = None
    if home_source == "config":
        executor["config"]["codex_home"] = "/var/accounts/saved-home"
    else:
        guardrails = {"labels": {"codex_home": {"initial": "/var/accounts/saved-home"}}}
    worker = {"name": "worker", "executor": executor}
    agent = await create_test_agent(
        client,
        executor=None if named_child else executor,
        guardrails=None if named_child else guardrails,
        sub_agents=[worker] if named_child else None,
    )
    body: dict[str, Any] = {"agent_id": agent["id"], "labels": {ACCOUNT_ID_LABEL_KEY: "codex-2"}}
    if named_child:
        body["parent_session_id"] = agent["_session_id"]
        body["sub_agent_name"] = "worker"

    saved_default = await client.post("/v1/sessions", json={**body, "labels": {}})
    assert saved_default.status_code == 201, saved_default.text
    assert saved_default.json()["labels"][CODEX_HOME_LABEL_KEY] == "/var/accounts/saved-home"
    response = await client.post("/v1/sessions", json=body)
    assert response.status_code == 201, response.text
    labels = response.json()["labels"]
    assert labels[ACCOUNT_ID_LABEL_KEY] == "codex-2"
    assert labels[CODEX_HOME_LABEL_KEY] == "/var/accounts/codex-2"

    # Explicitly conflicting caller inputs remain invalid, even with a saved home.
    body["labels"][CODEX_HOME_LABEL_KEY] = "/var/accounts/codex-1"
    conflict = await client.post("/v1/sessions", json=body)
    assert conflict.status_code == 400, conflict.text
    assert "conflicts" in conflict.text

    body["labels"] = {ACCOUNT_ID_LABEL_KEY: "missing"}
    invalid = await client.post("/v1/sessions", json=body)
    assert invalid.status_code == 400, invalid.text
    assert "Pinned account 'missing' is unknown" in invalid.text


@pytest.mark.asyncio
async def test_session_create_fails_when_pool_exhausted_without_silent_fallback(
    client: AsyncClient,
    account_pool_store: SqlAlchemyAccountPoolStore,
) -> None:
    """An exhausted pool fails the create instead of silently unassigning."""
    exhausted = account_pool_store.create_pool("pool-codex-x", "codex-x", "codex-native")
    account_pool_store.create_account(
        "codex-exhausted", exhausted.id, "/var/accounts/c-ex", home_kind=HOME_KIND_CODEX
    )
    _install_manager(
        client,
        account_pool_store,
        quota_provider=lambda _aid, _target: AccountQuota(status="exhausted"),
    )

    agent = await create_test_agent(client)
    resp = await client.post(
        "/v1/sessions",
        json={
            "agent_id": agent["id"],
            "title": "Exhausted Spawn",
            "harness_override": "codex-native",
        },
    )
    assert resp.status_code == 400
    assert "exhausted or unavailable" in resp.text


@pytest.mark.asyncio
async def test_real_parent_child_spawns_consume_shared_pool(
    client: AsyncClient,
    account_pool_store: SqlAlchemyAccountPoolStore,
) -> None:
    """Parent and child spawns share one pool and take different accounts."""
    manager = _install_manager(client, account_pool_store)
    agent = await create_test_agent(client)
    parent = await client.post(
        "/v1/sessions", json={"agent_id": agent["id"], "title": "Polly brain"}
    )
    assert parent.status_code == 201
    labels = []
    for index in range(2):
        response = await client.post(
            "/v1/sessions",
            json={
                "agent_id": agent["id"],
                "title": f"Child {index}",
                "parent_session_id": parent.json()["id"],
                "harness_override": "codex-native",
            },
        )
        assert response.status_code == 201, response.text
        labels.append(response.json()["labels"])
    assert [item[ACCOUNT_ID_LABEL_KEY] for item in labels] == ["codex-1", "codex-2"]
    assert manager.reservation_tracker.count_pending("codex-1") == 0
    assert manager.reservation_tracker.count_pending("codex-2") == 0


@pytest.mark.asyncio
async def test_endpoint_honors_explicit_home_and_rejects_identity_mismatch(
    client: AsyncClient,
    account_pool_store: SqlAlchemyAccountPoolStore,
) -> None:
    """A pinned credential home identifies the account; a mismatch fails."""
    _install_manager(client, account_pool_store)
    agent = await create_test_agent(client)
    body = {
        "agent_id": agent["id"],
        "title": "Home pin",
        "harness_override": "codex-native",
        "labels": {CODEX_HOME_LABEL_KEY: "/var/accounts/codex-2"},
    }
    response = await client.post("/v1/sessions", json=body)
    assert response.status_code == 201, response.text
    assert response.json()["labels"][ACCOUNT_ID_LABEL_KEY] == "codex-2"
    body["labels"][ACCOUNT_ID_LABEL_KEY] = "codex-1"
    response = await client.post("/v1/sessions", json=body)
    assert response.status_code == 400
    assert "conflicts" in response.text


@pytest.mark.asyncio
async def test_concurrent_endpoint_spawns_reserve_distinct_accounts(
    client: AsyncClient,
    account_pool_store: SqlAlchemyAccountPoolStore,
) -> None:
    """Concurrent spawns take different accounts."""
    import asyncio

    _install_manager(client, account_pool_store)
    agent = await create_test_agent(client)
    responses = await asyncio.gather(
        *[
            client.post(
                "/v1/sessions",
                json={
                    "agent_id": agent["id"],
                    "title": f"Parallel {index}",
                    "harness_override": "codex-native",
                },
            )
            for index in range(2)
        ]
    )
    assert all(response.status_code == 201 for response in responses), [
        response.text for response in responses
    ]
    assert sorted(response.json()["labels"][ACCOUNT_ID_LABEL_KEY] for response in responses) == [
        "codex-1",
        "codex-2",
    ]


@pytest.mark.asyncio
async def test_session_create_new_main_spec_resolution_regression(
    client: AsyncClient,
    account_pool_store: SqlAlchemyAccountPoolStore,
) -> None:
    """Account assignment works on the new-main path and honours a pin."""
    _install_manager(client, account_pool_store)

    agent = await create_test_agent(client, name="main-compat-agent")
    resp = await client.post(
        "/v1/sessions",
        json={
            "agent_id": agent["id"],
            "title": "Main Compat Session",
            "harness_override": "codex-native",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["labels"][ACCOUNT_ID_LABEL_KEY] == "codex-1"

    resp_pinned = await client.post(
        "/v1/sessions",
        json={
            "agent_id": agent["id"],
            "title": "Main Compat Pinned",
            "harness_override": "codex-native",
            "labels": {ACCOUNT_ID_LABEL_KEY: "codex-2"},
        },
    )
    assert resp_pinned.status_code == 201, resp_pinned.text
    assert resp_pinned.json()["labels"][ACCOUNT_ID_LABEL_KEY] == "codex-2"
