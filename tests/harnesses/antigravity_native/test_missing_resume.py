"""Conservative missing-history diagnosis and its delivered session events."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from omnigent.harnesses.antigravity_native import reader, rpc
from omnigent.server.schemas import SessionEventInput

SAVED_ID = "aaaaaaaa-1111-4222-8333-bbbbbbbbbbbb"
SESSION_ID = "conv_missing_resume"
OWN_PORT = 52548
FOREIGN_PORT = 52549


class ResumeScenario:
    """Drive the real discovery/supervisor with isolated files and HTTP transports."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.bridge_dir = tmp_path / "bridge"
        self.bridge_dir.mkdir()
        self.history.parent.mkdir(parents=True)
        self.set_id(SAVED_ID)
        (self.bridge_dir / "tmux.json").write_text(
            json.dumps({"socket_path": str(tmp_path / "tmux.sock"), "tmux_target": "main"})
        )
        self.now = 100.0
        self.sleeps: list[float] = []
        self.posts: list[dict] = []
        self.metadata_requests: list[tuple[float, int, dict]] = []
        self.server_requests: list[str] = []
        self.external_id: str | None = SAVED_ID
        self.pane = rpc.PaneAgyResolution(True, OWN_PORT)
        self.metadata_observations: list[httpx.Response | Exception] = []
        self.server_observations: list[httpx.Response | Exception] = []
        monkeypatch.setattr(reader, "monotonic", lambda: self.now, raising=False)
        monkeypatch.setattr(reader, "_sleep", self.sleep)
        monkeypatch.setattr(reader, "_candidate_agy_rpc_ports", list)
        monkeypatch.setattr(rpc, "resolve_pane_agy_rpc_port_state", lambda *args: self.pane)
        monkeypatch.setattr(
            rpc, "_rpc_headers", lambda *args: {"Content-Type": "application/json"}
        )
        monkeypatch.setattr(rpc, "_HTTP_TRANSPORT", httpx.MockTransport(self.metadata))

    @property
    def history(self) -> Path:
        return (
            self.bridge_dir / "agy-home/.gemini/antigravity-cli/conversations" / f"{SAVED_ID}.db"
        )

    def set_id(self, cascade_id: str) -> None:
        (self.bridge_dir / "state.json").write_text(
            json.dumps({"session_id": SESSION_ID, "conversation_id": cascade_id})
        )

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay

    def metadata(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/GetConversationMetadata")
        self.metadata_requests.append((self.now, request.url.port, json.loads(request.content)))
        observation = (
            self.metadata_observations.pop(0)
            if self.metadata_observations
            else httpx.Response(500, json={"code": "unknown", "message": "trajectory not found"})
        )
        if isinstance(observation, Exception):
            raise observation
        return observation

    def server(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            assert request.url.path == f"/v1/sessions/{SESSION_ID}"
            self.server_requests.append(request.url.path)
            if self.server_observations:
                observation = self.server_observations.pop(0)
                if isinstance(observation, Exception):
                    raise observation
                return observation
            return httpx.Response(200, json={"external_session_id": self.external_id})
        assert request.method == "POST"
        assert request.url.path == f"/v1/sessions/{SESSION_ID}/events"
        payload = json.loads(request.content)
        SessionEventInput.model_validate(payload)
        self.posts.append(payload)
        return httpx.Response(200, json={"queued": False})

    async def supervise(self, *, until: float = 140.0) -> None:
        async def on_pending(*args: object) -> None:
            pytest.fail("Missing history must not open an interaction")

        async with httpx.AsyncClient(
            base_url="http://omnigent.test", transport=httpx.MockTransport(self.server)
        ) as client:
            result = await reader.supervise_reader(
                self.bridge_dir,
                SESSION_ID,
                client=client,
                on_pending_interaction=on_pending,
                stop=lambda: self.now >= until,
            )
        assert result is None


@pytest.fixture
def scenario(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ResumeScenario:
    return ResumeScenario(tmp_path, monkeypatch)


@pytest.mark.asyncio
async def test_confirmed_missing_resume_delivers_failure_after_startup_grace(
    scenario: ResumeScenario,
) -> None:
    await scenario.supervise()

    assert scenario.now >= 130.0
    assert scenario.now < 140.0
    assert len(scenario.metadata_requests) >= 3
    assert all(
        port == OWN_PORT and body == {"conversationId": SAVED_ID}
        for _, port, body in scenario.metadata_requests
    )
    assert scenario.sleeps and set(scenario.sleeps) == {2.0}
    assert scenario.server_requests
    assert [post["type"] for post in scenario.posts] == [
        "external_conversation_item",
        "external_session_status",
    ]
    message, status = scenario.posts
    assert message["data"]["item_type"] == "message"
    assert message["data"]["item_data"]["role"] == "assistant"
    text = message["data"]["item_data"]["content"][0]["text"]
    assert SAVED_ID in text
    assert "history" in text.lower()
    assert status["data"] == {"status": "failed", "output": text, "turn_outcome": "failed"}
    assert (
        json.loads((scenario.bridge_dir / "state.json").read_text())["conversation_id"] == SAVED_ID
    )


@pytest.mark.asyncio
async def test_real_agy_missing_history_message_is_recognized(scenario: ResumeScenario) -> None:
    response = httpx.Response(
        500,
        json={
            "code": "unknown",
            "message": f"trajectory not found: {SAVED_ID} (error ID: synthetic-error)",
        },
    )
    scenario.metadata_observations = [response] * 30
    await scenario.supervise()
    assert scenario.posts[-1]["data"]["status"] == "failed"


@pytest.mark.parametrize("suffix", [".db-wal", ".pb"])
@pytest.mark.asyncio
async def test_alternate_native_history_is_not_reported_missing(
    scenario: ResumeScenario,
    suffix: str,
) -> None:
    scenario.history.with_suffix(suffix).write_bytes(b"saved native state")
    await scenario.supervise()
    assert scenario.posts == []


@pytest.mark.asyncio
async def test_startup_missing_history_waits_at_least_thirty_seconds(
    scenario: ResumeScenario,
) -> None:
    await scenario.supervise(until=128.0)
    assert scenario.metadata_requests
    assert scenario.posts == []


@pytest.mark.asyncio
async def test_missing_history_needs_repeated_confirms_after_transient_startup(
    scenario: ResumeScenario,
) -> None:
    scenario.metadata_observations = [httpx.ConnectError("RPC starting")] * 15
    await scenario.supervise()
    assert scenario.now == 134.0
    assert scenario.posts[-1]["data"]["status"] == "failed"


@pytest.mark.parametrize(
    "observation",
    [
        httpx.ConnectError("RPC disconnected"),
        httpx.Response(503, json={"message": "trajectory not found"}),
        httpx.Response(500, json={"message": "permission denied"}),
        httpx.Response(500, text="trajectory not found"),
        httpx.Response(500, json=["trajectory not found"]),
        httpx.Response(200, json={"metadata": {"rootConversationId": SAVED_ID}}),
    ],
    ids=["transport", "unavailable", "other-error", "not-json", "not-object", "nonmissing"],
)
@pytest.mark.asyncio
async def test_transient_or_nonmissing_metadata_resets_confirmation_count(
    scenario: ResumeScenario,
    observation: httpx.Response | Exception,
) -> None:
    missing = httpx.Response(500, json={"message": "trajectory not found"})
    scenario.metadata_observations = [missing] * 15 + [observation]
    await scenario.supervise(until=134.0)
    assert scenario.posts == []


@pytest.mark.parametrize("external_id", [None, "agy_conv_placeholder", "another-saved-id"])
@pytest.mark.asyncio
async def test_server_must_confirm_exact_saved_external_id(
    scenario: ResumeScenario,
    external_id: str | None,
) -> None:
    scenario.external_id = external_id
    await scenario.supervise()
    assert scenario.posts == []
    assert scenario.metadata_requests == []


@pytest.mark.parametrize(
    "observation",
    [
        httpx.ConnectError("server disconnected"),
        httpx.Response(503, json={"external_session_id": SAVED_ID}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"external_session_id": "another-saved-id"}),
    ],
    ids=["transport", "unavailable", "not-json", "changed-id"],
)
@pytest.mark.asyncio
async def test_server_confirmation_failure_resets_missing_count(
    scenario: ResumeScenario,
    observation: httpx.Response | Exception,
) -> None:
    scenario.server_observations = [
        httpx.Response(200, json={"external_session_id": SAVED_ID})
    ] * 15 + [observation]
    await scenario.supervise(until=134.0)
    assert scenario.posts == []


@pytest.mark.parametrize(
    "pane", [rpc.PaneAgyResolution(False, None), rpc.PaneAgyResolution(True, None)]
)
@pytest.mark.asyncio
async def test_foreign_agy_missing_response_cannot_prove_history_loss(
    scenario: ResumeScenario,
    monkeypatch: pytest.MonkeyPatch,
    pane: rpc.PaneAgyResolution,
) -> None:
    scenario.pane = pane
    monkeypatch.setattr(reader, "_candidate_agy_rpc_ports", lambda: [FOREIGN_PORT])
    await scenario.supervise()
    assert scenario.metadata_requests
    assert all(port == FOREIGN_PORT for _, port, _ in scenario.metadata_requests)
    assert scenario.posts == []


@pytest.mark.asyncio
async def test_absent_tmux_info_cannot_prove_history_loss(scenario: ResumeScenario) -> None:
    (scenario.bridge_dir / "tmux.json").unlink()
    await scenario.supervise()
    assert scenario.posts == []
    assert scenario.metadata_requests == []


@pytest.mark.parametrize("history_kind", ["existing", "inaccessible", "broken-symlink"])
@pytest.mark.asyncio
async def test_existing_or_inaccessible_local_history_does_not_fail(
    scenario: ResumeScenario,
    monkeypatch: pytest.MonkeyPatch,
    history_kind: str,
) -> None:
    scenario.history.parent.mkdir(parents=True, exist_ok=True)
    if history_kind == "existing":
        scenario.history.write_bytes(b"native history")
    elif history_kind == "broken-symlink":
        scenario.history.symlink_to(scenario.history.parent / "unavailable.db")
    else:
        original = Path.lstat

        def lstat(path: Path, *args: object, **kwargs: object):
            if path == scenario.history:
                raise PermissionError("native history inaccessible")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "lstat", lstat)
    await scenario.supervise()
    assert scenario.posts == []
    assert scenario.metadata_requests == []


@pytest.mark.asyncio
async def test_placeholder_is_startup_not_missing_resume(scenario: ResumeScenario) -> None:
    scenario.set_id("agy_conv_startup")
    await scenario.supervise()
    assert scenario.posts == []
    assert scenario.server_requests == []
    assert scenario.metadata_requests == []


@pytest.mark.asyncio
async def test_successful_discovery_preserves_existing_reader_without_server_client(
    scenario: ResumeScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reader, "_candidate_agy_rpc_ports", lambda: [OWN_PORT])
    scenario.metadata_observations = [
        httpx.Response(200, json={"metadata": {"rootConversationId": SAVED_ID}})
    ]
    result = await reader._discover(scenario.bridge_dir, poll_interval_s=0.25, stop=lambda: True)
    assert result == (SAVED_ID, OWN_PORT)
    assert scenario.sleeps == []
    assert scenario.server_requests == []


@pytest.mark.asyncio
async def test_discovery_without_client_cannot_confirm_missing_resume(
    scenario: ResumeScenario,
) -> None:
    result = await reader._discover(
        scenario.bridge_dir,
        poll_interval_s=0.25,
        stop=lambda: scenario.now >= 140.0,
    )
    assert result is None
    assert scenario.metadata_requests == []
