"""Real agy history and chat delivery survive runner exit and orphan cleanup."""

import contextlib
import os
import subprocess
import sys
import uuid
from pathlib import Path

import httpx
import psutil
import pytest
from playwright.sync_api import Page, expect

from omnigent.harnesses.antigravity_native import bridge
from tests.e2e_ui.shells.test_antigravity_tmux_recovery import (
    _antigravity_stack,
    _wait_until,
    antigravity_model,  # noqa: F401 -- imported pytest fixture
)

pytestmark = [
    pytest.mark.posix_only,
    pytest.mark.timeout(240),
    pytest.mark.skipif(
        os.environ.get("OMNIGENT_E2E_ANTIGRAVITY") != "mock",
        reason="set OMNIGENT_E2E_ANTIGRAVITY=mock for an isolated real-agy journey",
    ),
]


@pytest.mark.parametrize("damage_history", [False, True])
def test_chat_resumes_same_native_database_after_runner_exit(
    page: Page,
    tmp_path: Path,
    antigravity_model: list[str],  # noqa: F811
    damage_history: bool,
) -> None:
    with _antigravity_stack(tmp_path) as session:
        page.goto(f"{session.base_url}/c/{session.session_id}?view=chat")

        def reply() -> None:
            token = f"agy-e2e-{uuid.uuid4().hex[:8]}"
            composer = page.get_by_placeholder("Send a message…")
            composer.fill(f"Reply {token}. No tools.")
            page.get_by_role("button", name="Send", exact=True).click()
            bubble = page.locator('[data-testid="message-bubble"][data-role="assistant"]')
            expect(bubble.filter(has_text=token)).to_have_count(1, timeout=90_000)
            assert token in antigravity_model
            expect(page.get_by_test_id("working-indicator")).to_have_count(0, timeout=30_000)

        reply()
        url = f"{session.base_url}/v1/sessions/{session.session_id}"
        before = httpx.get(url, timeout=10).json()
        native_id = before["external_session_id"]
        assert native_id
        database = (
            bridge.agy_gemini_dir(session.bridge_dir)
            / "antigravity-cli/conversations"
            / f"{native_id}.db"
        )
        assert database.is_file()
        runner_env = psutil.Process(session.runner.pid).environ()
        runner_id = runner_env["OMNIGENT_RUNNER_ID"]
        session.runner.terminate()
        session.runner.wait(timeout=20)

        # This is the same sweep the host performs after idle runner exit.
        bridge.prune_orphaned_bridge_dirs()
        assert database.is_file(), "orphan cleanup deleted the resumable native database"
        if damage_history:
            assert database.is_relative_to(tmp_path)
            for suffix in ("", "-wal", "-shm"):
                Path(str(database) + suffix).unlink(missing_ok=True)
        resumed = None
        try:
            with (tmp_path / "resumed-runner.log").open("w") as log:
                resumed = subprocess.Popen(
                    [sys.executable, "-m", "omnigent.runner._entry"],
                    cwd=tmp_path,
                    env=runner_env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                session.runner = resumed

                def online() -> bool:
                    response = httpx.get(
                        f"{session.base_url}/v1/runners/{runner_id}/status", timeout=5
                    )
                    return response.status_code == 200 and response.json()["online"] is True

                _wait_until(online, "replacement runner did not connect", 60)
                page.reload()
                if damage_history:
                    composer = page.get_by_placeholder("Send a message…")
                    composer.fill("Reply Ready. No tools.")
                    page.get_by_role("button", name="Send", exact=True).click()
                    bubble = page.locator('[data-testid="message-bubble"][data-role="assistant"]')
                    expect(bubble.filter(has_text=native_id)).to_have_count(1, timeout=90_000)
                    failure = httpx.get(url, timeout=10).json()["last_task_error"]
                    assert failure is not None
                    assert native_id in failure["message"]
                    assert not database.exists()
                    return
                reply()
                after = httpx.get(url, timeout=10).json()
                assert after["external_session_id"] == native_id
                assert database.is_file()
        finally:
            if resumed is not None and resumed.poll() is None:
                resumed.terminate()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    resumed.wait(timeout=20)
                if resumed.poll() is None:
                    resumed.kill()
                    resumed.wait(timeout=10)
