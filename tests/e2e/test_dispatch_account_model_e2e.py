"""Runner tool -> HTTP -> real server proof for named, account-pinned children.

No harness is launched: this covers idle creation and persisted settings, without
credentials. The subprocess owns its database, agent catalog, and account pool.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from omnigent.account_pools.labels import ACCOUNT_ID_LABEL_KEY, CODEX_HOME_LABEL_KEY
from omnigent.runner import subagent_work
from omnigent.runner.tool_dispatch import execute_tool
from tests._helpers.live_server import isolated_local_server


@pytest.mark.asyncio
async def test_create_global_agent_by_name_with_account_model_and_effort(tmp_path: Path) -> None:
    """A registered global agent persists the requested account, model, and effort."""
    root = tmp_path / "server"
    account_home = tmp_path / "account"
    account_home.mkdir()
    agent = root / "server-home" / ".omnigent" / "agents" / "designer" / "config.yaml"
    agent.parent.mkdir(parents=True)
    agent.write_text(
        "spec_version: 1\nname: designer\nexecutor:\n"
        "  type: omnigent\n  config:\n    harness: codex-native\n"
        f"    codex_home: {account_home / 'codex-acc1'}\n",
        encoding="utf-8",
    )
    with isolated_local_server(root, extra_args=["--agent", str(agent.parent)]) as base_url:
        async with httpx.AsyncClient(base_url=base_url, trust_env=False, timeout=30) as client:
            pool = await client.post(
                "/v1/account-pools", json={"name": "codex-native", "harness": "codex-native"}
            )
            assert pool.status_code == 200, pool.text
            for account_id in ("codex-acc1", "codex-acc3"):
                home = account_home / account_id
                home.mkdir()
                account = await client.post(
                    f"/v1/account-pools/{pool.json()['id']}/accounts",
                    json={"id": account_id, "home_path": str(home), "home_kind": "codex_home"},
                )
                assert account.status_code == 200, account.text
            catalog = await client.get("/v1/agents")
            catalog.raise_for_status()
            agent_id = next(
                row["id"] for row in catalog.json()["data"] if row["name"] == "designer"
            )
            parent = await client.post(
                "/v1/sessions",
                json={"agent_id": agent_id, "labels": {ACCOUNT_ID_LABEL_KEY: "codex-acc1"}},
            )
            assert parent.status_code == 201, parent.text
            parent_id = parent.json()["id"]
            child_id = None
            try:
                created = json.loads(
                    await execute_tool(
                        tool_name="sys_session_create",
                        arguments=json.dumps(
                            {
                                "agent": "designer",
                                "account": "codex-acc3",
                                "model": "gpt-5.4",
                                "reasoning_effort": "high",
                                "title": "design-review",
                            }
                        ),
                        server_client=client,
                        conversation_id=parent_id,
                    )
                )
                assert "error" not in created, created
                child_id = created["conversation_id"]
                snapshot = await client.get(f"/v1/sessions/{child_id}")
                snapshot.raise_for_status()
                data = snapshot.json()
                assert data["parent_session_id"] == parent_id
                assert data["agent_id"] == agent_id
                assert data["model_override"] == "gpt-5.4"
                assert data["reasoning_effort"] == "high"
                assert data["labels"][ACCOUNT_ID_LABEL_KEY] == "codex-acc3"
                assert data["labels"][CODEX_HOME_LABEL_KEY] == str(account_home / "codex-acc3")
            finally:
                if child_id is not None:
                    subagent_work.unregister_subagent_work(child_id)
                    subagent_work.unregister_child_session(child_id)
                subagent_work._session_inboxes_ref.pop(parent_id, None)
