"""Guard OpenCode workers and Debby's separate flagship and optional bench rosters."""

from __future__ import annotations

from pathlib import Path

from omnigent.spec import load

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _sub_agents(bundle: str) -> dict[str, object]:
    spec = load(_REPO_ROOT / "examples" / bundle)
    return {sa.name: sa for sa in (getattr(spec, "sub_agents", None) or [])}


def _config(sub_agent: object) -> dict[str, object]:
    executor = getattr(sub_agent, "executor", None)
    config = getattr(executor, "config", None)
    if isinstance(config, dict):
        return config
    return {}


def test_polly_declares_opencode_worker() -> None:
    """Polly exposes an OpenCode worker on its native harness."""
    subs = _sub_agents("polly")
    assert "opencode" in subs
    assert _config(subs["opencode"]).get("harness") == "opencode-native"


def test_debby_keeps_flagship_heads_separate_from_opencode_bench() -> None:
    """Claude and GPT stay on their own harnesses; named OpenCode heads form the bench."""
    subs = _sub_agents("debby")
    assert "opencode" not in subs
    assert {"claude", "gpt"} <= set(subs)
    bench = {name for name in subs if name.startswith("opencode-")}
    assert bench == {"opencode-kimi", "opencode-glm", "opencode-qwen"}
    for name in bench:
        assert _config(subs[name]).get("harness") == "opencode-native"
    for name in ("claude", "gpt"):
        sub = subs[name]
        assert "opencode-native" not in (_config(sub).get("allowed_harnesses") or [])
