"""Structural test for the polly coding-orchestrator bundle (examples/polly).

polly is the standalone multi-agent coding orchestrator (successor to the
deleted nessie example, whose deep structural pins were folded in here).
Loads the bundle and asserts the distinctive wiring stays intact: the
claude-sdk orchestrator brain, the nine cross-vendor coding sub-agents
(claude_code / claude_sonnet / claude_haiku / codex / opencode /
opencode-minimax / opencode-glm / opencode-deepseek / agy, which implement,
review, and explore),
the three spine skills, and the bounds/blast-radius guardrails. Pure spec-load
— no LLM, no credentials.

What breaks if this fails:
- the orchestrator substrate drifts (model / harness / context window),
- a coding sub-agent is dropped or its harness changes (fewer implementers, or
  two collapse onto one vendor → cross-vendor review blind spot),
- a spine skill is dropped or renamed (the strategy layer regresses),
- a guardrail is removed (unbounded fan-out, or ungated push/deploy).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnigent.spec import load
from omnigent.spec.types import AgentSpec

# tests/e2e/omnigent/test_example_polly.py -> repo root is 3 parents up.
_POLLY_BUNDLE = Path(__file__).resolve().parents[3] / "examples" / "polly"


@pytest.fixture(scope="module")
def polly_spec() -> AgentSpec:
    """Load and validate the polly bundle once for the module."""
    return load(_POLLY_BUNDLE)


def test_orchestrator_executor(polly_spec: AgentSpec) -> None:
    """
    The orchestrator runs on claude-sdk with a 1M window and **no pinned
    model or profile**, so it inherits whatever Claude provider the user
    configured via ``omnigent setup --no-internal-beta`` (Anthropic key,
    subscription, gateway, or Databricks) and resolves that provider's
    default Claude model.

    Un-pinning is load-bearing for OSS (a Databricks-specific model id would
    404 on a plain Anthropic key). The old ``databricks-gpt-5-4`` fallback
    crash that a pin papered over is fixed at the root in
    ``chat.py`` ``_spec_declares_harness_or_model``, which recognizes the
    nested ``executor.config.harness`` and so never injects the ad-hoc
    default. Re-pinning a ``model`` here would re-couple polly to one
    provider — fail here so that regression is caught.

    Reads ``executor.config.harness`` (not a flat ``harness:``) because this
    is a bundle: a regression that drops the harness into a flat key would
    leave ``config.harness`` empty and fail here.
    """
    assert polly_spec.name == "polly"
    ex = polly_spec.executor
    assert ex.config.get("harness") == "claude-sdk"
    # No model pin — the configured provider's default Claude model is used.
    # Re-introducing a pin (Databricks or otherwise) fails here.
    assert ex.model is None
    # Profile is intentionally NOT pinned either.
    assert ex.profile is None
    assert ex.context_window == 1000000


def test_coding_subagents(polly_spec: AgentSpec) -> None:
    """
    The bundle has exactly nine coding sub-agents: ``claude_code`` /
    ``claude_sonnet`` / ``claude_haiku`` (claude-native), ``codex``
    (codex-native), ``opencode`` / ``opencode-minimax`` / ``opencode-glm`` /
    ``opencode-deepseek`` (opencode-native), and ``agy`` (antigravity-native) on
    the native terminal harnesses. All implement, review, and explore. The
    native harnesses render terminal-first (Chat / Terminal pill) so the human
    can watch or take over.

    The five pinned workers carry their model in ``executor.model``:
    claude_sonnet / claude_haiku pin their Claude ids, and the opencode-*
    workers pin provider/model ids verified against ``opencode models``.
    The unpinned trio (claude_code / codex / opencode / agy) run their
    harness default.

    A missing/renamed agent means fewer implementers, and same-vendor harnesses
    would break cross-vendor review — polly's differentiator.
    """
    fam = {a.name: a.executor.config.get("harness") for a in polly_spec.sub_agents}
    assert sorted(polly_spec.tools.agents) == [
        "agy",
        "claude_code",
        "claude_haiku",
        "claude_sonnet",
        "codex",
        "opencode",
        "opencode-deepseek",
        "opencode-glm",
        "opencode-minimax",
    ]
    assert fam["claude_code"] == "claude-native"
    assert fam["claude_sonnet"] == "claude-native"
    assert fam["claude_haiku"] == "claude-native"
    assert fam["codex"] == "codex-native"
    assert fam["opencode"] == "opencode-native"
    assert fam["opencode-minimax"] == "opencode-native"
    assert fam["opencode-glm"] == "opencode-native"
    assert fam["opencode-deepseek"] == "opencode-native"
    assert fam["agy"] == "antigravity-native"
    # Nine workers share four harnesses (claude-native x3, codex, opencode
    # x4, antigravity) — several workers per harness key, per the roster
    # preflight. Review independence is by MODEL vendor family, not harness:
    # Worker aliases select pins; review independence uses the resolved model
    # and vendor metadata after routing and args.model overrides.
    assert len(set(fam.values())) == 4
    # Each pinned worker's executor.model is pinned exactly as declared.
    by_name = {a.name: a for a in polly_spec.sub_agents}
    assert by_name["claude_sonnet"].executor.model == "claude-sonnet-5-5"
    assert by_name["claude_haiku"].executor.model == "claude-haiku-5-5"
    assert by_name["opencode-minimax"].executor.model == "opencode/minimax-m3.1-flash-free"
    assert by_name["opencode-glm"].executor.model == "opencode/glm-5.3-flash-free"
    assert by_name["opencode-deepseek"].executor.model == "opencode/deepseek-v4.1-flash-free"
    # Headless bypass knobs so workers don't stall on ApprovalCards.
    assert by_name["claude_code"].executor.config.get("permission_mode") == "auto"
    assert by_name["claude_sonnet"].executor.config.get("permission_mode") == "auto"
    assert by_name["claude_haiku"].executor.config.get("permission_mode") == "auto"
    assert by_name["claude_code"].executor.model is None
    assert by_name["codex"].executor.config.get("yolo") in (True, "True", "true")
    for name in (
        "claude_code",
        "claude_sonnet",
        "claude_haiku",
        "codex",
        "opencode",
        "opencode-minimax",
        "opencode-glm",
        "opencode-deepseek",
        "agy",
    ):
        prompt = (_POLLY_BUNDLE / "agents" / name / "config.yaml").read_text(encoding="utf-8")
        assert "IMPLEMENT — write real product code" in prompt
        assert "REVIEW — verify another agent's diff" in prompt
        assert "EXPLORE / SEARCH — answer a specific question" in prompt


def test_polly_preflights_host_harness_readiness() -> None:
    config = (_POLLY_BUNDLE / "config.yaml").read_text(encoding="utf-8")
    assert "sys_session_get_info({})" in config
    assert "`configured_harnesses` map" in config
    assert config.index("configured_harnesses") < config.index("command -v")


@pytest.mark.parametrize("relative_path", ["config.yaml", "skills/cross-review/SKILL.md"])
def test_review_independence_uses_resolved_models(relative_path: str) -> None:
    text = " ".join((_POLLY_BUNDLE / relative_path).read_text().split())
    assert "actual resolved model" in text
    assert "routing" in text and "args.model" in text
    assert "Never infer independence from worker names or harnesses" in text
    assert (
        "`opencode-minimax` overridden to a Claude model cannot review a Claude-built diff"
    ) in text
    assert "unknown family cannot establish independence" in text
    assert "fallback" in text and "sys_list_models" in text
    assert "`opencode-minimax` counts as OpenCode" not in text
    assert "trio all count as Claude" not in text


def test_polly_dev_skill_tracks_roster_and_readiness(polly_spec: AgentSpec) -> None:
    skill = _POLLY_BUNDLE.parents[1] / ".claude/skills/polly-e2e-dev/SKILL.md"
    text = " ".join(skill.read_text().split())
    assert "nine coding sub-agents" in text
    for worker in polly_spec.sub_agents:
        assert f"`{worker.name}`" in text
    assert "configured_harnesses" in text
    assert "exactly `true`" in text
    assert "absent or `null`" in text
    assert "actual resolved model" in text and "args.model" in text
    assert "unknown family cannot establish independence" in text


def test_spine_skills_present(polly_spec: AgentSpec) -> None:
    """All spine skills are discovered from skills/<dir>/SKILL.md."""
    assert sorted(s.name for s in polly_spec.skills) == [
        "cross-review",
        "fanout",
        "investigate",
    ]


def test_subagent_dispatch_text_advertises_task_titles_and_purpose(
    polly_spec: AgentSpec,
) -> None:
    """
    polly's prompt and workflow examples advertise task titles and purpose.

    If the purpose convention disappears, polly could spawn a sub-agent with
    no declared role, defeating the purpose guard. If the title convention
    disappears, sub-agent rows regress to generic vendor/harness names that
    are hard for humans to distinguish.
    """
    config = (_POLLY_BUNDLE / "config.yaml").read_text(encoding="utf-8")
    fanout = (_POLLY_BUNDLE / "skills" / "fanout" / "SKILL.md").read_text(encoding="utf-8")
    cross_review = (_POLLY_BUNDLE / "skills" / "cross-review" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    investigate = (_POLLY_BUNDLE / "skills" / "investigate" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "args.purpose" in config
    assert "Every `sys_session_send` MUST set both" in config
    assert "Name the sub-agent session for the work it is doing" in config
    assert "Bad titles are `claude_code`, `claude-code`, `codex`" in config
    assert "Goal mode is opt-in for long-running IMPLEMENT dispatches" in config
    assert "one standalone `/goal <condition>` command" in config
    assert "do not invent a `sys_session_send` goal parameter" in config
    assert (
        "long-running `claude_code`, `claude_sonnet`, `claude_haiku`, or `codex` implementation"
        in fanout
    )
    assert "Do not use child goal mode for other workers" in fanout
    assert 'purpose: "implement"' in fanout
    assert 'title="<task_slug>"' in fanout
    assert 'title="review-<task_slug>"' in cross_review
    assert 'purpose: "review"' in cross_review
    assert 'purpose: "implement"' in cross_review
    assert 'purpose: "explore"' in investigate
    assert 'purpose: "search"' in investigate
    config_compact = " ".join(config.split())
    assert "Collect finished worker results with `sys_read_inbox`" in config
    assert "do not use `sys_timer_set` or any delayed self-message" in config_compact
    assert "Collect its structured result with `sys_read_inbox`" in fanout
    assert "structured report with `sys_read_inbox`" in cross_review
    purpose_guard = next(
        p for p in polly_spec.guardrails.policies if p.name == "headless_subagent_purpose_guard"
    )
    assert purpose_guard.function.arguments["allowed_purposes"] == [
        "implement",
        "review",
        "explore",
        "search",
    ]


def test_orchestrator_keeps_timer_tool_but_forbids_worker_polling(
    polly_spec: AgentSpec,
) -> None:
    """
    polly keeps timer tools, but the prompt forbids polling workers with them.

    The timer surface is useful for genuine scheduled reminders / wall-clock
    delays. The prompt-level contract is that sub-agent waiting is handled by
    the async inbox auto-wake path, not by ``sys_timer_set`` status checks.
    """
    config = (_POLLY_BUNDLE / "config.yaml").read_text(encoding="utf-8")
    compact = " ".join(config.split())

    assert polly_spec.async_enabled is True
    assert polly_spec.timers is True
    assert "Timers remain available for genuine scheduled reminders" in compact
    assert "not for polling workers that already auto-wake you" in compact


def test_polly_test_count_ground_truth_guidance() -> None:
    """
    Polly prevents pytest count reconciliation from using source-function counts.

    Regression guard for #949: a prior orchestration handoff compared a worker's
    multi-file pytest total against ``grep -c 'def test_'`` from a single file,
    which misses parametrized case expansion and falsely labels accurate reports
    as over-counts. The contract must be present at the orchestrator layer, the
    cross-review workflow, while workers supply enough exact test context for
    Polly to verify the same gate.
    """
    config = (_POLLY_BUNDLE / "config.yaml").read_text(encoding="utf-8")
    cross_review = (_POLLY_BUNDLE / "skills" / "cross-review" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    config_compact = " ".join(config.split())
    cross_review_compact = " ".join(cross_review.split())
    for text in (config_compact, cross_review_compact):
        assert "python -m pytest --collect-only -q <same files>" in text
        assert "grep -c 'def test_'" in text
        assert "collected cases" in text
        assert "parametrized" in text

    assert "same command, same file set, and same commit" in config_compact
    assert "miscount`, `over-report`, or `fabrication`" in config_compact
    assert "exact file set/command/commit" in cross_review_compact

    for name in (
        "claude_code",
        "claude_sonnet",
        "claude_haiku",
        "codex",
        "opencode",
        "opencode-minimax",
        "opencode-glm",
        "opencode-deepseek",
        "agy",
    ):
        worker = (_POLLY_BUNDLE / "agents" / name / "config.yaml").read_text(encoding="utf-8")
        worker_compact = " ".join(worker.split())
        assert "When you report test results, include the exact command and file set" in (
            worker_compact
        ), name
        assert "distinguish collected test cases from test functions" in worker_compact, name


def test_orchestrator_forbids_premature_idle_after_announcing_intent() -> None:
    """
    The base prompt forbids ending a turn after only announcing intent.

    Regression guard for the premature-idle dropped-turn bug: the orchestrator
    brain ended its very first
    turn after a single intent sentence ("I'll load the cross-review skill and
    fetch the PR diff in parallel") without emitting any tool call, so nothing
    fanned out and — because no sub-agent was dispatched — no inbox auto-wake
    ever arrived to revive it. The whole decompose → fan-out → review →
    synthesize pipeline silently stalled. The fix is a strategy-layer rule
    ("mechanism is code; strategy is prompts + skills"): announcing a next
    action is not progress, so the tool calls that perform it must ride the
    SAME turn, and "end your turn" is licensed only once the dispatching calls
    are in flight. If this guidance regresses, the premature-idle dropped-turn
    class can return.
    """
    config = (_POLLY_BUNDLE / "config.yaml").read_text(encoding="utf-8")
    compact = " ".join(config.split())

    # The hard rule and its rationale (dropped turn → no fan-out → no wake).
    assert "Act in the SAME turn you announce" in compact
    assert "NEVER end a turn after only saying what you are about to do" in compact
    assert "no inbox wake will ever arrive to revive you" in compact
    # "End your turn" is disambiguated: only AFTER dispatch is in flight, never
    # a license to yield before dispatching anything.
    assert (
        'This "end your turn" applies only AFTER the dispatching tool calls are '
        "in flight; it is never a license to yield before you have dispatched "
        "anything"
    ) in compact
    # The first-turn case (the exact repro) is called out explicitly.
    assert "including right after a preamble on your FIRST turn — is a bug" in compact

    # Each spine skill reinforces "dispatch in this turn, then end the turn" so a
    # skill the brain loads can't reintroduce the announce-then-yield gap.
    fanout = (_POLLY_BUNDLE / "skills" / "fanout" / "SKILL.md").read_text(encoding="utf-8")
    cross_review = (_POLLY_BUNDLE / "skills" / "cross-review" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    investigate = (_POLLY_BUNDLE / "skills" / "investigate" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "never end a turn having only said you will dispatch" in " ".join(fanout.split())
    assert "never end a turn having only announced" in " ".join(cross_review.split())
    assert "do not end a turn having only said you will dispatch" in " ".join(investigate.split())


def test_orchestrator_delegates_substantive_work() -> None:
    """
    The base prompt forbids polly from doing coding work or investigations
    itself, while allowing direct non-code (docs/text/skill) authoring.

    This catches the regression where the orchestrator obeys a user asking
    "investigate this" by reading files / connector output directly instead of
    dispatching an ``explore`` or ``search`` worker and synthesizing from the
    worker's report, or where it edits source code itself instead of delegating.
    """
    config = (_POLLY_BUNDLE / "config.yaml").read_text(encoding="utf-8")
    compact = " ".join(config.split())

    # The hard rule: no code written directly; all coding work is delegated.
    assert "you do NOT write code — ALL coding work gets delegated" in compact
    assert "Any change to source code or tests, however small" in compact
    # Real investigation is delegated to explore/search workers...
    assert (
        'dispatch one or more sub-agents with `purpose: "explore"` or `purpose: "search"`'
    ) in compact
    assert (
        "ground your answer in their structured reports, not in your own deep "
        "shell/file/connector inspection"
    ) in compact
    # ...and polly's own sys_os_* tools may not be used to write code.
    assert (
        "Never use them to write or edit source code or tests, run a deep code "
        "investigation for your own answer, or merge a PR"
    ) in compact


def test_investigation_skill_delegates_read_only_work() -> None:
    """
    The ``investigate`` skill is a delegated workflow, not a direct-work recipe.

    If this skill stops dispatching ``explore`` / ``search`` sub-agents or
    starts permitting direct file/log/doc inspection by the orchestrator, polly
    will drift back into doing read-only work itself.
    """
    investigate = (_POLLY_BUNDLE / "skills" / "investigate" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    compact = " ".join(investigate.split())

    assert "Use for any read-only task: investigation, debugging, audit" in compact
    assert "Dispatch each task to `claude_code`, `claude_sonnet`, " in compact
    assert (
        '`sys_session_send(agent="claude_code"|"claude_sonnet"|"claude_haiku"'
        '|"codex"|"opencode"|"opencode-minimax"|"opencode-glm"'
        '|"opencode-deepseek"|"agy", '
        'title="explore-<task_slug>", '
        'args={purpose: "explore", '
        'input: "<question + exact scope + evidence requested>"})`'
    ) in compact
    # The audited skill (#3074) made task-based titles mandatory at dispatch.
    assert (
        "Use a task-based title such as `explore-ci-flake`, never the raw vendor name"
    ) in compact
    assert (
        'Use `purpose: "search"` only when the task is primarily external/document search'
    ) in compact
    assert ("Do not inspect files, logs, terminals, docs, or connector output yourself") in compact
    assert "collect their completion results with `sys_read_inbox`" in compact
    assert "Use `sys_session_get_history` only to debug" in compact
    assert "Synthesize only from those inbox-delivered reports" in compact
    assert (
        "must not answer the user's substantive question from its own direct file reads"
    ) in compact


def test_subagent_cancellation_guidance_present() -> None:
    """
    The orchestrator prompt and fanout skill teach polly how to stop workers.

    ``sys_session_send`` returns the child ``conversation_id`` as the task
    handle, and the runner's ``sys_cancel_task`` path hard-stops
    ``claude_code`` native workers while ``codex`` remains best-effort. If
    this guidance disappears, polly can abandon or re-dispatch work while the
    old worker keeps consuming resources in the background.
    """
    config = (_POLLY_BUNDLE / "config.yaml").read_text(encoding="utf-8")
    fanout = (_POLLY_BUNDLE / "skills" / "fanout" / "SKILL.md").read_text(encoding="utf-8")

    config_compact = " ".join(config.split())
    fanout_compact = " ".join(fanout.split())

    assert (
        "`sys_cancel_task` with `task_id` set to that sub-agent's recorded `conversation_id`"
    ) in config_compact
    assert "`claude_code` workers are hard-stopped" in config_compact
    assert "`codex` cancellation is currently best-effort" in config_compact
    assert "`agy` (`antigravity-native`) has no dedicated interrupt handler" in config_compact

    assert (
        "`sys_cancel_task` with `task_id` set to the recorded `conversation_id`"
    ) in fanout_compact
    assert (
        "The `claude-native` workers (`claude_code`, `claude_sonnet`, "
        "`claude_haiku`) are hard-stopped" in fanout_compact
    )
    assert "`codex` cancellation is best-effort" in fanout_compact


def test_orchestrator_guardrails(polly_spec: AgentSpec) -> None:
    """
    The orchestrator carries the spawn bound, the headless-purpose guard, and
    the blast-radius gate. ``spawn_bounds`` counts the sub-agent dispatch tool
    (``sys_session_send``) or fan-out is unbounded.
    """
    assert polly_spec.guardrails is not None
    names = sorted(p.name for p in polly_spec.guardrails.policies)
    assert names == [
        "blast_radius",
        "headless_subagent_purpose_guard",
        "spawn_bounds",
    ]
    spawn = next(p for p in polly_spec.guardrails.policies if p.name == "spawn_bounds")
    dispatch_tools = spawn.function.arguments.get("dispatch_tools")
    assert dispatch_tools is not None
    assert "sys_session_send" in dispatch_tools  # sub-agent sends are bounded
    # Nine workers (was four): a full parallel-safe wave can dispatch one
    # IMPLEMENT per worker in a single turn, so the cap must fit 9 — 10
    # leaves one spare for a same-turn auxiliary dispatch. Reviews land in
    # later turns as workers finish, so they don't stack on top of the wave.
    assert spawn.function.arguments.get("max_dispatches_per_turn") == 10


def test_subagent_guardrails(polly_spec: AgentSpec) -> None:
    """Each sub-agent carries the blast_radius gate (push/destructive)."""
    by_name = {a.name: a for a in polly_spec.sub_agents}
    for name in (
        "claude_code",
        "claude_sonnet",
        "claude_haiku",
        "codex",
        "opencode",
        "opencode-minimax",
        "opencode-glm",
        "opencode-deepseek",
        "agy",
    ):
        guardrails = by_name[name].guardrails
        assert guardrails is not None, name
        assert [p.name for p in guardrails.policies] == ["blast_radius"], name


def test_function_policies_have_nonempty_arguments(polly_spec: AgentSpec) -> None:
    """
    Every polly function-policy supplies a non-empty ``function.arguments``.

    Regression guard for a bug found in live testing: the resolver only calls
    the factory when arguments are truthy —
    ``target(**func_ref.arguments) if func_ref.arguments else target``
    (omnigent/policies/function.py). With empty ``arguments: {}`` the factory
    object itself is used as the evaluator, so the first gated tool call fails
    closed. Our policies are factories, so each must pass at least one argument.
    """
    specs = [polly_spec, *polly_spec.sub_agents]
    checked = 0
    for spec in specs:
        if spec.guardrails is None:
            continue
        for policy in spec.guardrails.policies:
            func_ref = getattr(policy, "function", None)
            if func_ref is None:
                continue  # not a function policy
            assert func_ref.arguments, (
                f"{spec.name}/{policy.name}: function.arguments is empty "
                f"({func_ref.arguments!r}); the resolver would use the factory itself "
                f"as the evaluator and the first gated tool call would fail closed."
            )
            checked += 1
    # orchestrator: blast_radius + spawn_bounds + headless_subagent_purpose_guard
    # = 3; sub-agents: blast_radius x9 (claude_code, claude_sonnet,
    # claude_haiku, codex, opencode, opencode-minimax, opencode-glm,
    # opencode-deepseek, agy) = 9 -> 12 total. Fewer = a policy dropped.
    assert checked == 12, f"expected 12 function policies in the bundle, inspected {checked}"
