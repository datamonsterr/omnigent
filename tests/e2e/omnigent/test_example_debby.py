"""Structural test for the Debby brainstorming panel (examples/debby).

Debby never answers from a single model: every question is fanned out by
default to BOTH a Claude sub-agent and a GPT sub-agent — two plain
(non-coding) responders on the claude-sdk and codex harnesses — and the
``debate`` skill has two partners critique each other before converging.
Five bench heads (claude_sonnet / claude_haiku on claude-sdk, opencode-kimi
/ opencode-glm / opencode-qwen on opencode-native) add vendor diversity on
request. Pure spec-load — no LLM, no credentials — modeled on
``test_example_polly.py``.

What breaks if this fails:
- the two flagship heads collapse onto one vendor (no cross-model contrast —
  Debby's whole point), or a head is dropped entirely,
- a head silently switches harness (e.g. the GPT head ends up on claude-sdk),
- the ``debate`` skill is dropped or renamed (the critique loop regresses),
- the ``os_env`` block disappears (the heads lose the file/shell tools the
  brainstorming surface relies on).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnigent.spec import load
from omnigent.spec.types import AgentSpec

# tests/e2e/omnigent/test_example_debby.py -> repo root is 3 parents up.
_DEBBY_BUNDLE = Path(__file__).resolve().parents[3] / "examples" / "debby"


@pytest.fixture(scope="module")
def debby_spec() -> AgentSpec:
    """Load and validate the debby bundle once for the module."""
    return load(_DEBBY_BUNDLE)


def test_debby_flagship_heads_cross_vendor(debby_spec: AgentSpec) -> None:
    """
    Debby's default pair is ``claude`` on claude-sdk and ``gpt`` on codex —
    two distinct vendors — plus five bench heads: ``claude_sonnet`` /
    ``claude_haiku`` (claude-sdk) and ``opencode-kimi`` / ``opencode-glm`` /
    ``opencode-qwen`` (opencode-native).

    A missing/renamed head, or both flagships landing on the same harness,
    removes the cross-model contrast that is Debby's entire reason to exist.
    """
    assert debby_spec.name == "debby"
    fam = {a.name: a.executor.config.get("harness") for a in debby_spec.sub_agents}
    assert sorted(debby_spec.tools.agents) == [
        "claude",
        "claude_haiku",
        "claude_sonnet",
        "gpt",
        "opencode-glm",
        "opencode-kimi",
        "opencode-qwen",
    ]
    assert fam["claude"] == "claude-sdk"
    assert fam["claude_sonnet"] == "claude-sdk"
    assert fam["claude_haiku"] == "claude-sdk"
    assert fam["gpt"] == "codex"
    assert fam["opencode-kimi"] == "opencode-native"
    assert fam["opencode-glm"] == "opencode-native"
    assert fam["opencode-qwen"] == "opencode-native"
    # The flagships span two distinct vendors → the default answers always
    # contrast across providers.
    assert fam["claude"] != fam["gpt"]
    # Each bench head's executor.model is pinned exactly as declared.
    by_name = {a.name: a for a in debby_spec.sub_agents}
    assert by_name["claude_sonnet"].executor.model == "claude-sonnet-5-5"
    assert by_name["claude_haiku"].executor.model == "claude-haiku-5-5"
    assert by_name["opencode-kimi"].executor.model == "opencode-go/kimi-k3"
    assert by_name["opencode-glm"].executor.model == "opencode-go/glm-5.3"
    assert by_name["opencode-qwen"].executor.model == "opencode-go/qwen3.8-max"


def test_debby_flagship_heads_are_unpinned(debby_spec: AgentSpec) -> None:
    """
    The flagship heads pin no model: each inherits whatever Claude / OpenAI
    provider the user configured (Anthropic key, subscription, gateway, or
    Databricks). The bench heads DO pin (see the flagship test above).

    Un-pinning is load-bearing for OSS — a Databricks-specific model id would
    404 on a plain Anthropic / OpenAI key. Re-introducing a flagship pin
    re-couples a head to one provider, so fail here if a model reappears.
    """
    by_name = {a.name: a for a in debby_spec.sub_agents}
    for name in ("claude", "gpt"):
        assert by_name[name].executor.model is None, name
        assert by_name[name].executor.profile is None, name


def test_debby_debate_skill_present(debby_spec: AgentSpec) -> None:
    """The ``debate`` skill is discovered from skills/debate/SKILL.md."""
    assert sorted(s.name for s in debby_spec.skills) == ["debate"]


def test_debby_has_os_env(debby_spec: AgentSpec) -> None:
    """
    Debby carries an ``os_env`` block so the bridged ``sys_os_*`` tools register
    for the brainstorming surface. The shipped sandbox is ``type: none`` so the
    bundle loads on macOS too. Dropping ``os_env`` would leave the heads with no
    file/shell tools at all.
    """
    assert debby_spec.os_env is not None
    assert debby_spec.os_env.type == "caller_process"
    assert debby_spec.os_env.sandbox is not None
    assert debby_spec.os_env.sandbox.type == "none"
