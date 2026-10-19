"""Unreadable native history must survive real orphan maintenance."""

from pathlib import Path

import pytest

from omnigent.harnesses.antigravity_native import bridge


def test_scandir_error_retains_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "bridges"
    session = root / "saved"
    history = session / "agy-home/.gemini/antigravity-cli/conversations"
    history.mkdir(parents=True)
    (history / "saved.db-wal").write_bytes(b"uncheckpointed history")
    (session / "owner.pid").write_text("99999999")
    monkeypatch.setattr(bridge, "_BRIDGE_ROOT", root)
    monkeypatch.setattr("omnigent.inner.terminal._process_alive", lambda _: False)
    monkeypatch.setattr(
        "omnigent.harness_plugins.native_agents",
        lambda: [type("Agent", (), {"key": "antigravity"})()],
    )
    original = bridge.os.scandir

    def unavailable(path):
        if Path(path) == history:
            raise PermissionError("cannot read saved history")
        return original(path)

    monkeypatch.setattr(bridge.os, "scandir", unavailable)
    assert bridge.prune_orphaned_bridge_dirs() == 0
    assert session.exists()


def test_unavailable_linked_home_is_retained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "bridges"
    session = root / "saved"
    session.mkdir(parents=True)
    (session / "agy-home").symlink_to(tmp_path / "offline-native-history")
    (session / "owner.pid").write_text("99999999")
    monkeypatch.setattr(bridge, "_BRIDGE_ROOT", root)
    monkeypatch.setattr("omnigent.inner.terminal._process_alive", lambda _: False)
    assert bridge.prune_orphaned_bridge_dirs() == 0
    assert (session / "agy-home").is_symlink()
