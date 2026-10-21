"""Owned static model tables for Smart Routing and background titles.

Pre-launch picker listings carry no static stand-ins anymore — the live
harness probes (see ``omnigent.host.connect``) are their source of truth.
What remains here is the router's operational data: rankings, arm menus,
probed exclusions that no discovery API can provide, plus the economy-tier
arms background session-title generation pins per harness family.
"""

from __future__ import annotations

from dataclasses import dataclass

from omnigent.onboarding.provider_config import SUBSCRIPTION_KIND


@dataclass(frozen=True)
class StaticModelFallback:
    """A release-curated model list with auditable ownership and provenance."""

    model_ids: tuple[str, ...]
    owner: str
    provenance: str
    discovery_gap: str


#: Curated preference ORDER for codex's current arms — a ranking hint only
#: (preferred first), consumed by the Databricks live-discovery ranker to
#: sort servable ids. It never invents picker rows: ids absent from the live
#: listing are simply not ranked by it.
_CODEX_ARM_PREFERENCE = StaticModelFallback(
    model_ids=("gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.5"),
    owner="Databricks model discovery (omnigent.models.databricks_model_discovery)",
    provenance="Omnigent's release-curated Codex arm ordering",
    discovery_gap="a workspace listing ranks models by neither recency nor capability",
)

_STATIC_MODEL_FALLBACKS: dict[tuple[str, str], StaticModelFallback] = {
    (SUBSCRIPTION_KIND, "codex"): _CODEX_ARM_PREFERENCE,
}


def static_model_fallback(provider_kind: str, cli: str) -> StaticModelFallback | None:
    """Return the owned fallback table for a provider kind and CLI, if registered."""
    return _STATIC_MODEL_FALLBACKS.get((provider_kind, cli))


#: Codex's launch default when nothing else names a model. The bundled
#: OpenAI catalog's newest row is a bare family alias (``gpt-5.6``) that
#: codex rejects, so a codex launch defaults to a concrete variant from
#: codex's own catalog — dotted spelling, since the Databricks hyphenated
#: form 400s against codex's own backend.
_CODEX_LAUNCH_DEFAULT = StaticModelFallback(
    model_ids=("gpt-5.6-sol",),
    owner="Codex native launch",
    provenance="codex's catalog slug for Omnigent's preferred launch arm",
    discovery_gap=(
        "the launch default is resolved before any app-server probe can "
        "answer, and codex rejects the bundled catalog's newest row (a bare "
        "family alias)"
    ),
)

CODEX_DEFAULT_MODEL = _CODEX_LAUNCH_DEFAULT.model_ids[0]


# ── Smart Routing ───────────────────────────────────────────────────────────
#
# The router's static tables. A live per-session catalog wins wherever one is
# in reach (``omnigent.server.smart_routing.fetch_runner_models``) and a
# deployment's ``routing.*`` settings override each table wholesale; these are
# what a router that can reach neither falls back to.

_SMART_ROUTING_FALLBACKS: dict[str, StaticModelFallback] = {
    "claude_ladder": StaticModelFallback(
        model_ids=(
            "databricks-claude-haiku-4-5",
            "databricks-claude-sonnet-4-6",
            "databricks-claude-sonnet-5",
            "databricks-claude-opus-4-8",
        ),
        owner="Smart Routing (omnigent.server.smart_routing)",
        provenance="Unity Gateway Claude serving endpoints, cheapest → most powerful",
        discovery_gap=(
            "the router picks before a session's live model catalog is reachable, "
            "and a gateway listing ranks models by neither cost nor capability"
        ),
    ),
    "gpt_ladder": StaticModelFallback(
        model_ids=(
            "databricks-gpt-5-4-nano",
            "databricks-gpt-5-4-mini",
            "databricks-gpt-5-4",
            "databricks-gpt-5-5",
        ),
        owner="Smart Routing (omnigent.server.smart_routing)",
        provenance="Unity Gateway GPT serving endpoints, cheapest → most powerful",
        discovery_gap=(
            "the router picks before a session's live model catalog is reachable, "
            "and a gateway listing ranks models by neither cost nor capability"
        ),
    ),
    "pi_ladder": StaticModelFallback(
        model_ids=(
            "databricks-gpt-5-4-nano",
            "databricks-claude-haiku-4-5",
            "databricks-gpt-5-4-mini",
            "databricks-claude-sonnet-4-6",
            "databricks-claude-sonnet-5",
            "databricks-gpt-5-4",
            "databricks-gpt-5-5",
            "databricks-claude-opus-4-8",
        ),
        owner="Smart Routing (omnigent.server.smart_routing)",
        provenance="the Claude and GPT ladders interleaved by cost, for multi-model pi",
        discovery_gap=(
            "the router picks before a session's live model catalog is reachable, "
            "and a gateway listing ranks models by neither cost nor capability"
        ),
    ),
    "current_generation_gpt": StaticModelFallback(
        model_ids=(
            "databricks-glm-5-2",
            "databricks-gpt-5-6-luna",
            "databricks-gpt-5-6-sol",
        ),
        owner="Smart Routing (omnigent.server.smart_routing)",
        provenance="the external router's own current arms, offered so a pick keeps its endpoint",
        discovery_gap=(
            "the router picks before a session's live model catalog is reachable, "
            "and a gateway listing ranks models by neither cost nor capability"
        ),
    ),
    "task_v1_claude_arms": StaticModelFallback(
        model_ids=("claude-opus-4-8", "claude-sonnet-5"),
        owner="Smart Routing (omnigent.server.smart_routing)",
        provenance="the task_v1 router's Claude arm menu, which it requires in full",
        discovery_gap="the router's arm menu is part of its request contract, not a catalog",
    ),
    "task_v1_codex_arms": StaticModelFallback(
        model_ids=("glm-5-2", "gpt-5-6-sol", "gpt-5-6-luna"),
        owner="Smart Routing (omnigent.server.smart_routing)",
        provenance="the task_v1 router's codex arm menu, which it requires in full",
        discovery_gap="the router's arm menu is part of its request contract, not a catalog",
    ),
    "family_fallbacks": StaticModelFallback(
        model_ids=("claude-sonnet-5", "gpt-5-6-luna"),
        owner="Smart Routing (omnigent.server.smart_routing)",
        provenance="one arm per family (claude, gpt), both frozen members of the task_v1 menus",
        discovery_gap="a workspace that serves no endpoint for the picked arm needs a pinned one",
    ),
    "pi_excluded": StaticModelFallback(
        model_ids=(
            "databricks-claude-haiku-4-5",
            "databricks-gpt-5-5",
            "databricks-gpt-5-5-pro",
            "databricks-gpt-5-6-luna",
            "databricks-gpt-5-6-terra",
            "databricks-gpt-5-6-sol",
        ),
        owner="Smart Routing (omnigent.server.smart_routing)",
        provenance="probed: pi's own gateway 400s on each of these",
        discovery_gap="a gateway listing advertises these without pi's request-shape limits",
    ),
    "codex_native_excluded": StaticModelFallback(
        model_ids=("databricks-gpt-5-4-nano", "system.ai.databricks-gpt-5-4-nano"),
        owner="Native Codex Smart Routing",
        provenance="the model rejects native tool_search input with invalid_request_error",
        discovery_gap="model availability listings omit native tool-search compatibility",
    ),
    "codex_catalog_clone_source": StaticModelFallback(
        model_ids=("gpt-5.6-luna",),
        owner="Codex extended catalog (omnigent.inner.codex_executor)",
        provenance="codex's own bundled catalog slug for the cheapest current arm",
        discovery_gap="codex's bundled catalog carries no entry for a gateway-only arm to clone",
    ),
}

#: Claude serving endpoints the router ranks, cheapest → most powerful.
SMART_ROUTING_CLAUDE_LADDER = _SMART_ROUTING_FALLBACKS["claude_ladder"].model_ids

#: GPT serving endpoints the router ranks, cheapest → most powerful.
SMART_ROUTING_GPT_LADDER = _SMART_ROUTING_FALLBACKS["gpt_ladder"].model_ids

#: Both ladders interleaved by cost, for the multi-model pi harness.
SMART_ROUTING_PI_LADDER = _SMART_ROUTING_FALLBACKS["pi_ladder"].model_ids

#: The router's own current gpt-family arms (GLM included), offered as
#: candidates so a routed arm resolves to its own endpoint.
SMART_ROUTING_CURRENT_GENERATION_GPT = _SMART_ROUTING_FALLBACKS["current_generation_gpt"].model_ids

#: The ``task_v1`` router's Claude arm menu, most powerful first.
SMART_ROUTING_TASK_V1_CLAUDE_ARMS = _SMART_ROUTING_FALLBACKS["task_v1_claude_arms"].model_ids

#: The ``task_v1`` router's codex arm menu.
SMART_ROUTING_TASK_V1_CODEX_ARMS = _SMART_ROUTING_FALLBACKS["task_v1_codex_arms"].model_ids

#: One fixed fallback arm per family, ordered ``(claude, gpt)``.
SMART_ROUTING_FAMILY_FALLBACKS = _SMART_ROUTING_FALLBACKS["family_fallbacks"].model_ids

#: Models pi's own gateway rejects, so the router may not pick them under pi.
SMART_ROUTING_PI_EXCLUDED = _SMART_ROUTING_FALLBACKS["pi_excluded"].model_ids

#: Models incompatible with native Codex's deferred tool-search requests and history.
SMART_ROUTING_CODEX_NATIVE_EXCLUDED = _SMART_ROUTING_FALLBACKS["codex_native_excluded"].model_ids

#: The codex catalog entry a gateway-only arm is cloned from.
CODEX_CATALOG_CLONE_SOURCE_SLUG = _SMART_ROUTING_FALLBACKS["codex_catalog_clone_source"].model_ids[
    0
]

#: Cheapest current arm per CLI family for background session titles. Title
#: calls are tiny (<=64 output tokens, no tools, low effort), so they always
#: run on the family's cheapest arm regardless of the session's model.
_BACKGROUND_TITLE_FALLBACKS: dict[str, StaticModelFallback] = {
    "claude": StaticModelFallback(
        model_ids=("haiku",),
        owner="Background session titles (omnigent.runner.background_titles.service)",
        provenance=(
            "claude's bare family alias resolves to the cheapest current Haiku "
            "on Anthropic-direct and AI-Gateway paths alike"
        ),
        discovery_gap=(
            "a background title never consults the session's live model catalog, "
            "and no discovery API ranks arms by cost"
        ),
    ),
    "codex": StaticModelFallback(
        model_ids=("gpt-5.6-luna",),
        owner="Background session titles (omnigent.runner.background_titles.service)",
        provenance=(
            "codex's own dotted catalog slug for its cheapest current arm — the "
            "gateway's hyphenated spelling 400s on codex's backend"
        ),
        discovery_gap=(
            "a background title never consults the session's live model catalog, "
            "and no discovery API ranks arms by cost"
        ),
    ),
}

#: The claude-family arm background session titles pin.
BACKGROUND_TITLE_CLAUDE_ECONOMY_MODEL = _BACKGROUND_TITLE_FALLBACKS["claude"].model_ids[0]

#: The codex-family arm background session titles pin.
BACKGROUND_TITLE_CODEX_ECONOMY_MODEL = _BACKGROUND_TITLE_FALLBACKS["codex"].model_ids[0]


# OpenCode's effort dial uses a curated ranking unavailable in model discovery.
_OPENCODE_HAIKU_FAMILY = StaticModelFallback(
    model_ids=("claude-haiku",),
    owner="OpenCode effort routing",
    provenance="Family prefix used by the fork's OpenCode effort dial",
    discovery_gap="Model discovery lists ids without their effort-family grouping",
)

_OPENCODE_DEFAULT_TIERS = StaticModelFallback(
    model_ids=(
        "opencode-go/longcat-2.5-preview-free",
        "opencode-go/deepseek-v4-flash",
        "opencode-go/muse-spark-1.3-contributor",
    ),
    owner="OpenCode effort routing",
    provenance="Fork-curated low, medium, and high effort defaults",
    discovery_gap="Model discovery does not rank the operator's effort tiers",
)

_OPENCODE_FAMILY_TIERS: dict[str, StaticModelFallback] = {
    "deepseek": StaticModelFallback(
        model_ids=(
            "opencode-go/deepseek-v4-flash",
            "opencode-go/deepseek-v4.1-flash",
            "opencode-go/deepseek-v4-pro",
        ),
        owner="OpenCode effort routing",
        provenance="Fork-curated low, medium, and high effort family tiers",
        discovery_gap="Model discovery does not rank the operator's effort tiers",
    ),
    "muse-spark": StaticModelFallback(
        model_ids=(
            "opencode-go/muse-spark-1.2-contributor",
            "opencode-go/muse-spark-1.2-contributor",
            "opencode-go/muse-spark-1.3-contributor",
        ),
        owner="OpenCode effort routing",
        provenance="Fork-curated low, medium, and high effort family tiers",
        discovery_gap="Model discovery does not rank the operator's effort tiers",
    ),
    "kimi": StaticModelFallback(
        model_ids=(
            "opencode-go/kimi-k2.7-code",
            "opencode-go/kimi-k2.7-code",
            "opencode-go/kimi-k3",
        ),
        owner="OpenCode effort routing",
        provenance="Fork-curated low, medium, and high effort family tiers",
        discovery_gap="Model discovery does not rank the operator's effort tiers",
    ),
    "glm": StaticModelFallback(
        model_ids=("opencode-go/glm-5.3-flash", "opencode-go/glm-5.2", "opencode-go/glm-5.3"),
        owner="OpenCode effort routing",
        provenance="Fork-curated low, medium, and high effort family tiers",
        discovery_gap="Model discovery does not rank the operator's effort tiers",
    ),
    "gpt": StaticModelFallback(
        model_ids=(
            "opencode-go/gpt-5.6-luna",
            "opencode-go/gpt-5.6-luna",
            "opencode-go/gpt-6-luna",
        ),
        owner="OpenCode effort routing",
        provenance="Fork-curated low, medium, and high effort family tiers",
        discovery_gap="Model discovery does not rank the operator's effort tiers",
    ),
    "grok": StaticModelFallback(
        model_ids=("opencode-go/grok-4.6", "opencode-go/grok-4.6", "opencode-go/grok-4.7"),
        owner="OpenCode effort routing",
        provenance="Fork-curated low, medium, and high effort family tiers",
        discovery_gap="Model discovery does not rank the operator's effort tiers",
    ),
    "longcat": StaticModelFallback(
        model_ids=(
            "opencode-go/longcat-2.0",
            "opencode-go/longcat-2.5-preview-free",
            "opencode-go/longcat-2.5-preview-free",
        ),
        owner="OpenCode effort routing",
        provenance="Fork-curated low, medium, and high effort family tiers",
        discovery_gap="Model discovery does not rank the operator's effort tiers",
    ),
    "mimo": StaticModelFallback(
        model_ids=(
            "opencode-go/mimo-v2.5",
            "opencode-go/mimo-v2.6-flash",
            "opencode-go/mimo-v2.6-pro",
        ),
        owner="OpenCode effort routing",
        provenance="Fork-curated low, medium, and high effort family tiers",
        discovery_gap="Model discovery does not rank the operator's effort tiers",
    ),
    "minimax": StaticModelFallback(
        model_ids=(
            "opencode-go/minimax-m2.7",
            "opencode-go/minimax-m2.7",
            "opencode-go/minimax-m3",
        ),
        owner="OpenCode effort routing",
        provenance="Fork-curated low, medium, and high effort family tiers",
        discovery_gap="Model discovery does not rank the operator's effort tiers",
    ),
    "qwen": StaticModelFallback(
        model_ids=(
            "opencode-go/qwen3.8-flash",
            "opencode-go/qwen3.7-plus",
            "opencode-go/qwen3.8-max",
        ),
        owner="OpenCode effort routing",
        provenance="Fork-curated low, medium, and high effort family tiers",
        discovery_gap="Model discovery does not rank the operator's effort tiers",
    ),
    "hy": StaticModelFallback(
        model_ids=("opencode-go/hy3", "opencode-go/hy4-preview", "opencode-go/hy4-preview"),
        owner="OpenCode effort routing",
        provenance="Fork-curated low, medium, and high effort family tiers",
        discovery_gap="Model discovery does not rank the operator's effort tiers",
    ),
    _OPENCODE_HAIKU_FAMILY.model_ids[0]: StaticModelFallback(
        model_ids=(
            "opencode-go/claude-haiku-5-5",
            "opencode-go/claude-haiku-5-5",
            "opencode-go/claude-haiku-5-5",
        ),
        owner="OpenCode effort routing",
        provenance="Fork-curated low, medium, and high effort family tiers",
        discovery_gap="Model discovery does not rank the operator's effort tiers",
    ),
    "space-bunny": StaticModelFallback(
        model_ids=(
            "opencode-go/space-bunny",
            "opencode-go/space-bunny",
            "opencode-go/space-bunny",
        ),
        owner="OpenCode effort routing",
        provenance="Fork-curated low, medium, and high effort family tiers",
        discovery_gap="Model discovery does not rank the operator's effort tiers",
    ),
}

OPENCODE_EFFORT_DEFAULT_TIER = dict(
    zip(("low", "medium", "high"), _OPENCODE_DEFAULT_TIERS.model_ids, strict=True)
)
OPENCODE_EFFORT_FAMILY_TIERS = {
    family: dict(zip(("low", "medium", "high"), fallback.model_ids, strict=True))
    for family, fallback in _OPENCODE_FAMILY_TIERS.items()
}

_ANTIGRAVITY_SUPPORTED_MODELS = StaticModelFallback(
    model_ids=("claude-sonnet-4-6", "claude-opus-4-6-thinking", "gpt-oss-120b-medium"),
    owner="Antigravity model validation",
    provenance="Non-Gemini model ids supported by the native Antigravity catalog",
    discovery_gap="Spec validation runs before a host model catalog is available",
)
ANTIGRAVITY_SUPPORTED_MODELS = frozenset(_ANTIGRAVITY_SUPPORTED_MODELS.model_ids)
