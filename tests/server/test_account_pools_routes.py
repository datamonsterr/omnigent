"""Tests for the account pool API routes (``/v1/account-pools``)."""

from __future__ import annotations

import time

import pytest
from httpx import AsyncClient

from omnigent.account_pools.labels import ACCOUNT_ID_LABEL_KEY
from omnigent.entities.account_pool import AccountQuota


@pytest.fixture()
def store(client: AsyncClient):
    """The account pool store the server reads.

    :param client: The HTTP client.
    :returns: The store.
    """
    return client._transport.app.state.account_pool_store


@pytest.mark.asyncio
async def test_create_and_list_pools(client: AsyncClient, store) -> None:
    """A created pool is listed with its accounts."""
    response = await client.post(
        "/v1/account-pools",
        json={"name": "codex-native", "harness": "codex-native"},
    )
    assert response.status_code == 200, response.text
    created = response.json()
    assert created["name"] == "codex-native"

    listing = await client.get("/v1/account-pools")
    assert listing.status_code == 200
    data = listing.json()["data"]
    assert any(pool["name"] == "codex-native" for pool in data)


@pytest.mark.asyncio
async def test_duplicate_pool_name_is_rejected(client: AsyncClient, store) -> None:
    """A second pool with the same name fails."""
    body = {"name": "codex-native", "harness": "codex-native"}
    assert (await client.post("/v1/account-pools", json=body)).status_code == 200
    second = await client.post("/v1/account-pools", json=body)
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_create_account_and_list_with_usage(client: AsyncClient, store, tmp_path) -> None:
    """An account is created and shows up in the account list."""
    pool = (
        await client.post("/v1/account-pools", json={"name": "p1", "harness": "codex-native"})
    ).json()
    home = tmp_path / "codex-acc1"
    home.mkdir()
    response = await client.post(
        f"/v1/account-pools/{pool['id']}/accounts",
        json={"id": "codex-acc1", "home_path": str(home), "home_kind": "codex_home"},
    )
    assert response.status_code == 200, response.text
    account = response.json()
    assert account["id"] == "codex-acc1"
    assert account["enabled"] is True

    accounts = await client.get("/v1/accounts")
    assert accounts.status_code == 200
    rows = accounts.json()["data"]
    assert any(row["id"] == "codex-acc1" for row in rows)
    usage = next(row for row in rows if row["id"] == "codex-acc1")["usage"]
    assert usage["sessions"] == 0
    assert usage["active_sessions"] == 0


@pytest.mark.asyncio
async def test_account_with_missing_home_is_rejected(client: AsyncClient, store) -> None:
    """A home path that does not exist is refused."""
    pool = (
        await client.post("/v1/account-pools", json={"name": "p2", "harness": "codex-native"})
    ).json()
    response = await client.post(
        f"/v1/account-pools/{pool['id']}/accounts",
        json={"id": "codex-acc2", "home_path": "/definitely/not/here", "home_kind": "codex_home"},
    )
    assert response.status_code == 400
    assert "not an existing directory" in response.text


@pytest.mark.asyncio
async def test_unknown_home_kind_is_rejected(client: AsyncClient, store, tmp_path) -> None:
    """An unknown credential home kind is refused."""
    pool = (
        await client.post("/v1/account-pools", json={"name": "p3", "harness": "codex-native"})
    ).json()
    response = await client.post(
        f"/v1/account-pools/{pool['id']}/accounts",
        json={"id": "codex-acc3", "home_path": str(tmp_path), "home_kind": "bogus"},
    )
    assert response.status_code == 400
    assert "Unknown home kind" in response.text


@pytest.mark.asyncio
async def test_update_and_delete_account(client: AsyncClient, store, tmp_path) -> None:
    """An account can be disabled and removed."""
    pool = (
        await client.post("/v1/account-pools", json={"name": "p4", "harness": "codex-native"})
    ).json()
    home = tmp_path / "codex-acc4"
    home.mkdir()
    await client.post(
        f"/v1/account-pools/{pool['id']}/accounts",
        json={"id": "codex-acc4", "home_path": str(home), "home_kind": "codex_home"},
    )

    updated = await client.patch(
        "/v1/accounts/codex-acc4",
        json={"enabled": False},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["enabled"] is False

    deleted = await client.delete("/v1/accounts/codex-acc4")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True

    missing = await client.get("/v1/accounts/codex-acc4")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_delete_pool_removes_accounts(client: AsyncClient, store, tmp_path) -> None:
    """Deleting a pool takes its accounts out of the inventory."""
    pool = (
        await client.post("/v1/account-pools", json={"name": "p5", "harness": "codex-native"})
    ).json()
    home = tmp_path / "codex-acc5"
    home.mkdir()
    await client.post(
        f"/v1/account-pools/{pool['id']}/accounts",
        json={"id": "codex-acc5", "home_path": str(home), "home_kind": "codex_home"},
    )
    deleted = await client.delete(f"/v1/account-pools/{pool['id']}")
    assert deleted.status_code == 200
    accounts = await client.get("/v1/accounts")
    assert all(row["id"] != "codex-acc5" for row in accounts.json()["data"])


@pytest.mark.asyncio
async def test_usage_counts_sessions_by_account(client: AsyncClient, store, tmp_path) -> None:
    """A session's account shows up in the usage rows."""
    from tests.server.helpers import create_test_agent

    agent = await create_test_agent(client)
    pool = store.create_pool("pool-codex", "codex", "codex-native")
    store.create_account(
        "codex-acc1", pool.id, str(tmp_path / "codex-acc1"), home_kind="codex_home"
    )
    store.save_quota_snapshot("codex-acc1", AccountQuota(observed_at=int(time.time())))
    created = await client.post(
        "/v1/sessions",
        json={
            "agent_id": agent["id"],
            "title": "Accounted Session",
            "harness_override": "codex-native",
            "labels": {ACCOUNT_ID_LABEL_KEY: "codex-acc1"},
        },
    )
    assert created.status_code == 201, created.text

    accounts = await client.get("/v1/accounts?period_days=7")
    assert accounts.status_code == 200
    rows = accounts.json()["data"]
    account = next(row for row in rows if row["id"] == "codex-acc1")
    assert account["usage"]["sessions"] == 1
    assert account["quota"]["exhausted"] is False
