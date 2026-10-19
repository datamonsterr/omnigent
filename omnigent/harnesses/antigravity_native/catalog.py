"""Live model catalog for the native Antigravity (agy) harness.

Queries ``agy models`` once per TTL window and caches the result on the host
process so repeated picker requests are cheap.  Failures retry on the next
request; they never raise out of the public API so the host's model-options
lane can degrade gracefully.

Output contract (``agy models`` stdout):
    One model per line, TAB-separated: ``<slug>\\t<display label>``.
    Progress/status messages go to stderr and are ignored here.
    The ``--output-format`` flag is NOT supported by agy and must not be used.

Cache identity includes the executable, credential/settings metadata, and a
hash of relevant environment selectors. Secret values are never logged.

Timeout:
    Subprocess is bounded by :data:`_PROBE_TIMEOUT_S`; a timeout counts as a
    transient failure. A recent answer from the same configuration can be
    reused for at most five minutes; otherwise the caller receives ``None``.
"""

from __future__ import annotations

import copy
import hashlib
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

_logger = logging.getLogger(__name__)

# How long a successful catalog listing stays valid (seconds).
# Short enough to pick up a model added during a session; long enough to keep
# repeated picker requests free of subprocess overhead.
_CATALOG_TTL_S: float = 300.0

# Hard wall on how long we wait for ``agy models`` to return.
# agy models is a local list operation — it should be fast.  10 s is generous.
_PROBE_TIMEOUT_S: float = 10.0

# OAuth token file locations (Linux / macOS) that serve as the auth-change
# signal in the cache key.  We use mtime only — the secret bytes never leave
# the file.
_LINUX_OAUTH_TOKEN: Path = Path.home() / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
_MACOS_OAUTH_CREDS: Path = Path.home() / ".gemini" / "oauth_creds.json"

# Serialize concurrent host probes; to_thread callers can run simultaneously.
_cache_models: list[dict[str, object]] | None = None
_cache_stamp: float = 0.0
_cache_key: tuple[object, ...] | None = None
_cache_lock = threading.RLock()
_MAX_STALE_S = 300.0


def _oauth_token_mtime() -> float | None:
    """Return the mtime of whichever OAuth credential file exists, or None.

    Checks the Linux path first (``antigravity-oauth-token``), then the macOS
    path (``oauth_creds.json``).  Returns ``None`` when neither exists so the
    cache key is still defined and does not prematurely invalidate on systems
    that use a keychain-only credential (no file written).

    :returns: File mtime as a float, or ``None`` when no file is found.
    """
    for path in (_LINUX_OAUTH_TOKEN, _MACOS_OAUTH_CREDS):
        try:
            return path.stat().st_mtime
        except OSError:
            pass
    return None


def _agy_exe_mtime(agy_path: str) -> float | None:
    """Return the mtime of the agy executable, or None on error.

    :param agy_path: Absolute path to the agy binary.
    :returns: File mtime as a float, or ``None`` when the stat fails.
    """
    try:
        return Path(agy_path).stat().st_mtime
    except OSError:
        return None


def _parse_agy_models_output(stdout: str) -> list[dict[str, object]]:
    """Parse ``agy models`` tab-separated stdout into model dicts.

    Each non-empty line must have the form ``<slug>\\t<display label>``.
    Lines with a different number of fields are skipped with a warning so a
    future format change degrades rather than crashes.

    :param stdout: Raw stdout text from ``agy models``.
    :returns: List of model dicts with ``id``, ``model``, and ``displayName``
        keys. The CLI's listing does not identify a default model.
        ``reasoningEffortLevels`` is deliberately
        omitted — effort levels are already baked into each slug
        (e.g. ``gemini-3.8-flash-high``).
    """
    models: list[dict[str, object]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t", 1)
        if len(parts) != 2:
            _logger.warning("Unexpected agy model catalog format; skipping a malformed row")
            continue
        slug, label = parts[0].strip(), parts[1].strip()
        if not slug or not label or any(m["id"] == slug for m in models):
            continue
        entry: dict[str, object] = {
            "id": slug,
            "model": slug,
            "displayName": label,
        }
        models.append(entry)
    return models


def _sanitize_frame(exc: BaseException) -> str:
    """Return a safe single-line summary of *exc* for logging.

    Strips any potential credential fragments (long hex/base64 tokens) from
    the exception message so probe failures never log raw auth material.

    :param exc: The caught exception.
    :returns: A sanitized string, at most 200 characters.
    """
    import re

    raw = f"{type(exc).__name__}: {exc}"
    # Mask anything that looks like a token (20+ word chars without spaces).
    sanitized = re.sub(r"\b[A-Za-z0-9_\-]{20,}\b", "<redacted>", raw)
    return sanitized[:200]


def probe_agy_model_catalog(
    *,
    agy_path: str | None = None,
    ttl: float = _CATALOG_TTL_S,
    timeout: float = _PROBE_TIMEOUT_S,
) -> list[dict[str, object]] | None:
    """Serialize probes and return copies of successful catalogs for this configuration."""
    with _cache_lock:
        return copy.deepcopy(_probe_agy_model_catalog(agy_path=agy_path, ttl=ttl, timeout=timeout))


def _file_identity(path: Path) -> tuple[str, int, int] | None:
    try:
        info = path.stat()
        return str(path.resolve()), info.st_size, info.st_mtime_ns
    except OSError:
        return None


def _probe_agy_model_catalog(
    *,
    agy_path: str | None = None,
    ttl: float = _CATALOG_TTL_S,
    timeout: float = _PROBE_TIMEOUT_S,
) -> list[dict[str, object]] | None:
    """Return the live ``agy models`` catalog, with TTL caching.

    Calls ``agy models`` in a subprocess, parses the tab-separated output, and
    caches the result for *ttl* seconds.  The cache is invalidated when the
    agy executable or the OAuth token file changes on disk.

    Failed probes may reuse a recent answer from the same configuration.
    They never extend its expiry or return another account's catalog.

    :param agy_path: Explicit path to the agy binary.  When ``None`` the
        function resolves it via :func:`shutil.which` / the default fallback.
    :param ttl: Cache TTL in seconds.  Override in tests to force re-probes.
    :param timeout: Subprocess timeout in seconds.
    :returns: List of model dicts, or ``None`` when no catalog is available.
    """
    global _cache_models, _cache_stamp, _cache_key

    # Resolve the binary path once so cache-key computation and exec use the
    # same path without calling which twice.
    resolved_path = agy_path or shutil.which("agy")
    if resolved_path is None:
        # Try the default installer fallback.
        fallback = Path.home() / ".local" / "bin" / "agy"
        resolved_path = str(fallback) if fallback.is_file() else None

    now = time.monotonic()
    current_key = (
        str(Path(resolved_path).resolve()) if resolved_path else None,
        _agy_exe_mtime(resolved_path) if resolved_path else None,
        _oauth_token_mtime(),
        _file_identity(_LINUX_OAUTH_TOKEN),
        _file_identity(_MACOS_OAUTH_CREDS),
        _file_identity(Path.home() / ".gemini/antigravity-cli/settings.json"),
        hashlib.sha256(
            repr(
                tuple(
                    os.environ.get(name)
                    for name in (
                        "GEMINI_API_KEY",
                        "ANTIGRAVITY_API_KEY",
                        "GOOGLE_GEMINI_BASE_URL",
                        "AGY_ADC_AUTH",
                        "GOOGLE_APPLICATION_CREDENTIALS",
                        "GOOGLE_CLOUD_PROJECT",
                    )
                )
            ).encode()
        ).hexdigest(),
    )

    def previous_catalog() -> list[dict[str, object]] | None:
        # An outage may reuse a recent answer from this configuration only.
        return (
            _cache_models
            if _cache_key == current_key and now - _cache_stamp < _MAX_STALE_S
            else None
        )

    # Return cached result when still fresh AND the key hasn't changed.
    if _cache_models is not None and now - _cache_stamp < ttl and _cache_key == current_key:
        return _cache_models

    if resolved_path is None:
        _logger.warning("agy binary not found; cannot probe model catalog")
        return None

    # Run the probe.
    try:
        result = subprocess.run(
            [resolved_path, "models"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
    except subprocess.TimeoutExpired:
        _logger.warning("agy models timed out after %.0fs; using cached catalog", timeout)
        return previous_catalog()
    except subprocess.CalledProcessError as exc:
        _logger.warning(
            "agy models exited with code %d; using cached catalog",
            exc.returncode,
        )
        return previous_catalog()
    except OSError as exc:
        _logger.warning(
            "agy models failed to launch (%s); using cached catalog",
            type(exc).__name__,
        )
        return previous_catalog()
    except Exception as exc:
        _logger.warning(
            "agy models probe failed (%s); using cached catalog",
            type(exc).__name__,
        )
        return previous_catalog()

    models = _parse_agy_models_output(result.stdout)
    if not models:
        _logger.warning("agy models returned no models; using cached catalog")
        return previous_catalog()

    _cache_models = models
    _cache_stamp = time.monotonic()
    _cache_key = current_key
    return _cache_models


def clear_catalog_cache() -> None:
    """Reset the module-level catalog cache.

    Intended for tests and for callers that want to force a fresh probe on
    the next :func:`probe_agy_model_catalog` call.
    """
    global _cache_models, _cache_stamp, _cache_key
    with _cache_lock:
        _cache_models = None
        _cache_stamp = 0.0
        _cache_key = None
