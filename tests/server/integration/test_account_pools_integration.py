"""Integration tests for native account pool selection in session creation."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
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
    app: FastAPI,
    account_pools_config: AccountPoolsConfig,
):
    # Attach account pool manager to app state
    mgr = AccountPoolManager(config=account_pools_config)
    app.state.account_pool_manager = mgr

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

    # 2. Second session spawn for codex picks codex-2 (fewest active sessions)
    # Simulate session 1 being active (running)
    from omnigent.runtime import get_conversation_store

    conv_store = get_conversation_store()
    conv_store.set_session_live_status(data1["id"], "running")

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
    app: FastAPI,
    account_pools_config: AccountPoolsConfig,
):
    mgr = AccountPoolManager(config=account_pools_config)
    app.state.account_pool_manager = mgr

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
