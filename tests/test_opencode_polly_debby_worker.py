"""Guards the OpenCode worker's presence in the shipped example agents.

Both polly and debby once declared an optional ``opencode`` sub-agent
(``harness: opencode-native``). Shipping a sub-agent whose harness older
clients don't recognize made every old runner/host fail to launch the agent at
all — the version-skew incident behind omnigent-ai/omnigent#1145. That incident
is now mitigated on the execution path: ``spec.load(...,
prune_invalid_sub_agents=True)`` (runner ``_entry`` + server ``agent_cache``)
gracefully DROPS a sub-agent whose harness a client doesn't recognize, so an old
client loads polly with its remaining workers instead of failing. Combined with
``opencode-native`` now being a recognized harness
(``omnigent.spec._omnigent_compat.OMNIGENT_HARNESSES``), polly re-declares its
``opencode`` worker; the positive test below guards that it stays wired.

debby, however, is still deliberately opencode-free (reverted in #1295), and the
negative test below guards that OpenCode does not creep back into that spec.
"""

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


def _polly_prompt() -> str:
    return load(_REPO_ROOT / "examples" / "polly").instructions


def test_polly_declares_opencode_worker() -> None:
    """polly declares its ``opencode`` worker on the ``opencode-native`` harness.

    Safe to re-add because #1145's graceful pruning drops the worker (rather than
    failing the whole agent) on any client too old to recognize the harness, and
    ``opencode-native`` is a recognized harness on current clients. If this ever
    regresses to failing old clients, prune-on-load is the contract to check.
    """
    subs = _sub_agents("polly")
    assert "opencode" in subs
    assert _config(subs["opencode"]).get("harness") == "opencode-native"


def test_polly_discovers_native_model_catalogs_before_dispatch() -> None:
    """Native workers are routed from their own current catalogs."""
    prompt = " ".join(_polly_prompt().split())
    investigate_skill = (
        _REPO_ROOT / "examples" / "polly" / "skills" / "investigate" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "before the first respective dispatch" in prompt
    assert "`opencode models --verbose`" in prompt
    assert "`agy models`" in prompt
    dispatch_guidance = prompt.index("Delegate ALL coding work")
    assert prompt.index("`opencode models --verbose`") < dispatch_guidance
    assert prompt.index("`agy models`") < dispatch_guidance
    assert "returned model IDs verbatim" in prompt
    assert "tool calling, input modality, context capacity, and reasoning depth" in prompt
    assert "unknown required capability makes that model ineligible" in prompt
    assert "stronger reasoning plus sufficient context for hard implementation" in prompt
    assert "task-fit catalog shortlist in that agent entry's `models` field" in prompt
    assert "native catalog selections must always set it to that returned ID" in prompt
    assert "empty or unsupported for `opencode-native` and `antigravity-native`" in prompt
    assert "shared `sys_list_models` may be empty or unsupported" in investigate_skill


def test_polly_recovers_opencode_payment_failure_once() -> None:
    """An OpenCode payment failure gets one verified-free retry, never a guess."""
    prompt = " ".join(_polly_prompt().split())
    recovery = prompt.split("- An OpenCode Go ", 1)[1].split("- Claude and Gemini quotas", 1)[0]

    assert "`Insufficient funds`" in recovery
    assert "payment failure, not a boot failure" in recovery
    assert "Only for that exact error, run exactly one" in recovery
    assert recovery.count("`opencode models --refresh --verbose`") == 1
    assert "active and tool-call-capable" in recovery
    assert "complete numeric cost fields" in recovery
    assert "input, output, cache read/write" in recovery
    assert "all of those costs are exactly zero" in recovery
    assert "Missing, unknown, null, or non-numeric cost data is ineligible" in recovery
    assert "A `-free` suffix or similar name alone is not proof" in recovery
    assert "Retry the task once" in recovery
    assert "fresh session with a new, unique task title" in recovery
    assert "explicit `args.model`" in recovery
    assert "omit `reasoning_effort`" in recovery
    assert "static effort tiers can silently remap the chosen ID to a paid model" in recovery
    assert "Never choose an unverified paid alternative" in recovery
    assert "no suitable verified-free model or that one retry fails" in recovery
    assert "another AVAILABLE worker or report the blocker; do not loop" in recovery
    assert "A model is immutable for an existing sub-agent conversation" in prompt
    assert "continuations reuse its title without `args.model`" in prompt


def test_polly_recovers_antigravity_quota_by_actual_model_vendor() -> None:
    """Antigravity retries once across its independent Claude/Gemini quotas."""
    prompt = " ".join(_polly_prompt().split())
    recovery = prompt.split("- Claude and Gemini quotas", 1)[1]
    agy_config = (_REPO_ROOT / "examples" / "polly" / "agents" / "agy" / "config.yaml").read_text(
        encoding="utf-8"
    )

    assert "Claude and Gemini models" in prompt
    assert "Claude and Gemini quotas are separate" in prompt
    cross_review = (
        _REPO_ROOT / "examples" / "polly" / "skills" / "cross-review" / "SKILL.md"
    ).read_text(encoding="utf-8")

    assert "one suitable model from the other family" in recovery
    assert "rerun `agy models` once to refresh" in recovery
    assert "fresh session with a new, unique task title" in recovery
    assert "explicit `args.model` set to the returned slug" in recovery
    assert "Catalog presence does not prove quota" in recovery
    assert "do not retry quota recovery again" in recovery
    assert "automatic account selection" in recovery
    assert "unless the human explicitly pinned one" in recovery
    assert "route to another AVAILABLE worker or report the blocker" in recovery
    assert "AGY running a Claude model is not an independent reviewer" in prompt
    assert "returned slug" in prompt
    assert "encoded reasoning tier" in prompt
    assert "Claude and Gemini models" in agy_config
    assert "actual model vendor" in cross_review
    assert "AGY Claude cannot" in cross_review
    assert "independently review `claude_code`" in cross_review


def test_polly_failure_recovery_preserves_boot_gate_and_worktree_isolation() -> None:
    """Model recovery cannot revive a broken harness or reuse committed partial work."""
    prompt = " ".join(_polly_prompt().split())
    fanout = (_REPO_ROOT / "examples" / "polly" / "skills" / "fanout" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "authentication failures stay UNAVAILABLE" in prompt
    assert "never sent through payment/quota recovery" in prompt
    assert "record the worktree's starting HEAD and confirm it is clean" in prompt
    assert "HEAD still matches that starting commit" in prompt
    assert "`git status --short --untracked-files=all` is empty" in prompt
    assert "Otherwise preserve it for diagnosis" in prompt
    assert "dispatch replacement work in a clean worktree" in prompt
    assert "use polly's failure recovery rules" in fanout
    assert "HEAD matches" in fanout


def test_debby_does_not_declare_opencode_head() -> None:
    """debby stays opencode-free, so an older client can load it without skew.

    debby is reverted to its two-head roster (claude + gpt). Re-adding an
    ``opencode`` head (or any ``opencode-native`` harness override) would
    reintroduce the harness that broke old clients on spec validation.
    """
    subs = _sub_agents("debby")
    assert "opencode" not in subs
    assert {"claude", "gpt"} <= set(subs)
    config_text = (_REPO_ROOT / "examples" / "debby" / "config.yaml").read_text(encoding="utf-8")
    assert "opencode" not in config_text.lower()
    # No head re-introduces opencode-native via a harness override either.
    for sub in subs.values():
        assert "opencode-native" not in (_config(sub).get("allowed_harnesses") or [])
