"""Integration tests for native account pool selection in session creation."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from omnigent.account_pools.manager import AccountPoolManager
from omnigent.account_pools.models import (
    ACCOUNT_ID_LABEL_KEY,
    ACCOUNT_POOL_LABEL_KEY,
    CODEX_HOME_LABEL_KEY,
    GEMINI_DIR_LABEL_KEY,
    AccountConfig,
    AccountPool,
    AccountPoolsConfig,
)
from tests.server.helpers import create_test_agent


@pytest.fixture
def account_pools_config() -> AccountPoolsConfig:
    return AccountPoolsConfig(
        pools={
            "codex": AccountPool(
                name="codex",
                accounts=[
                    AccountConfig(id="codex-1", codex_home=Path("/var/accounts/codex-1")),
                    AccountConfig(id="codex-2", codex_home=Path("/var/accounts/codex-2")),
                ],
            ),
            "antigravity": AccountPool(
                name="antigravity",
                accounts=[
                    AccountConfig(id="agy-1", gemini_dir=Path("/var/accounts/agy-1/.gemini")),
                    AccountConfig(id="agy-2", gemini_dir=Path("/var/accounts/agy-2/.gemini")),
                ],
            ),
        }
    )


@pytest.mark.asyncio
async def test_session_create_picks_account_and_forwards_env(
    client: AsyncClient,
    account_pools_config: AccountPoolsConfig,
):
    # Test real endpoint integration through RuntimeCaps
    from omnigent.runtime import get_caps

    caps = get_caps()
    caps.account_pools = account_pools_config

    # Create agent
    agent = await create_test_agent(client)
    agent_id = agent["id"]

    # 1. First session spawn for codex picks codex-1 and forwards codex_home
    resp1 = await client.post(
        "/v1/sessions",
        json={
            "agent_id": agent_id,
            "title": "Codex Task 1",
            "harness_override": "codex-native",
        },
    )
    assert resp1.status_code == 201
    data1 = resp1.json()
    labels1 = data1.get("labels", {})
    assert labels1.get(ACCOUNT_ID_LABEL_KEY) == "codex-1"
    assert labels1.get(ACCOUNT_POOL_LABEL_KEY) == "codex"
    assert labels1.get(CODEX_HOME_LABEL_KEY) == str(Path("/var/accounts/codex-1").resolve())

    # 2. Second session spawn for codex picks codex-2 automatically!
    # Because session 1 is in startup launch window (< 120s in DB), codex-1 is active!
    resp2 = await client.post(
        "/v1/sessions",
        json={
            "agent_id": agent_id,
            "title": "Codex Task 2",
            "harness_override": "codex-native",
        },
    )
    assert resp2.status_code == 201
    data2 = resp2.json()
    labels2 = data2.get("labels", {})
    assert labels2.get(ACCOUNT_ID_LABEL_KEY) == "codex-2"
    assert labels2.get(CODEX_HOME_LABEL_KEY) == str(Path("/var/accounts/codex-2").resolve())

    # 3. Third session spawn with explicit account pin always honors pin
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
    account_pools_config: AccountPoolsConfig,
):
    from omnigent.runtime import get_caps

    caps = get_caps()
    caps.account_pools = account_pools_config

    agent = await create_test_agent(client)
    agent_id = agent["id"]

    resp = await client.post(
        "/v1/sessions",
        json={
            "agent_id": agent_id,
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
async def test_session_create_fails_when_pool_exhausted_without_silent_fallback(
    client: AsyncClient,
):
    # Configure an exhausted pool for codex
    from omnigent.account_pools.quota import AccountQuota
    from omnigent.runtime import get_caps

    exhausted_pool = AccountPoolsConfig(
        pools={
            "codex": AccountPool(
                name="codex",
                accounts=[
                    AccountConfig(id="codex-exhausted", codex_home=Path("/var/accounts/c-ex")),
                ],
            )
        }
    )
    caps = get_caps()
    caps.account_pools = exhausted_pool

    # Attach manager with exhausted quota provider
    mgr = AccountPoolManager(
        config=exhausted_pool,
        quota_provider=lambda _aid, _acc: AccountQuota(status="exhausted"),
    )
    from omnigent.runtime import get_caps

    caps.account_pools = exhausted_pool

    agent = await create_test_agent(client)
    agent_id = agent["id"]

    # Inject manager on app state
    client._transport.app.state.account_pool_manager = mgr

    # Spawn must fail clearly with 400 and NOT silently fall back to an unassigned session
    resp = await client.post(
        "/v1/sessions",
        json={
            "agent_id": agent_id,
            "title": "Exhausted Spawn",
            "harness_override": "codex-native",
        },
    )
    assert resp.status_code == 400
    assert "All accounts in pool 'codex' are exhausted or unavailable" in resp.text


@pytest.mark.asyncio
async def test_real_parent_child_spawns_consume_shared_pool(client, account_pools_config):
    from omnigent.runtime import get_caps

    get_caps().account_pools = account_pools_config
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
    manager = client._transport.app.state.account_pool_manager
    assert manager.reservation_tracker.count_pending("codex-1") == 0
    assert manager.reservation_tracker.count_pending("codex-2") == 0


@pytest.mark.asyncio
async def test_endpoint_honors_explicit_home_and_rejects_identity_mismatch(
    client, account_pools_config
):
    from omnigent.runtime import get_caps

    get_caps().account_pools = account_pools_config
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
async def test_concurrent_endpoint_spawns_reserve_distinct_accounts(client, account_pools_config):
    import asyncio

    from omnigent.account_pools.quota import AccountQuota
    from omnigent.runtime import get_caps

    get_caps().account_pools = account_pools_config
    manager = AccountPoolManager(
        config=account_pools_config, quota_provider=lambda *_: AccountQuota()
    )
    client._transport.app.state.account_pool_manager = manager
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
    client: AsyncClient, account_pools_config: AccountPoolsConfig
):
    """Regression test: verify account assignment on new-main without spec_for_env NameError."""
    from omnigent.runtime import get_caps

    caps = get_caps()
    caps.account_pools = account_pools_config
    if hasattr(client._transport.app.state, "account_pool_manager"):
        delattr(client._transport.app.state, "account_pool_manager")

    # Normal agent with loaded spec resolves spec_for_env from selection_spec
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

    # Pinned account resolves and preserves pin on new-main
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
