"""Runner launch must preserve explicitly pinned free OpenCode models."""

from __future__ import annotations

from pathlib import Path

import pytest

_FREE_OPENCODE_MODELS = (
    "opencode/minimax-m3.1-flash-free",
    "opencode/glm-5.3-flash-free",
    "opencode/deepseek-v4.1-flash-free",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("model_id", _FREE_OPENCODE_MODELS)
async def test_native_launch_config_keeps_free_model_pin_with_effort(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    model_id: str,
) -> None:
    """The persisted launch model stays exact when no free effort tier is known."""
    from omnigent.runner.native import orchestration

    async def fetch_snapshot(**_kwargs: object) -> dict[str, object]:
        return {
            "workspace": str(tmp_path),
            "model_override": model_id,
            "reasoning_effort": "high",
            "labels": {},
        }

    monkeypatch.setenv("RUNNER_SERVER_URL", "http://localhost:6767")
    monkeypatch.setattr(orchestration, "_fetch_native_launch_snapshot", fetch_snapshot)

    config = await orchestration._opencode_native_launch_config(
        session_id="conv-free-opencode",
        server_client=None,
    )

    assert config.model_override == model_id
