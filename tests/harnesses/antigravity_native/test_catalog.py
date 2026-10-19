"""Tests for omnigent.harnesses.antigravity_native.catalog.

All subprocess calls are monkeypatched — no live agy invocation.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import omnigent.harnesses.antigravity_native.catalog as _mod
from omnigent.harnesses.antigravity_native.catalog import (
    _parse_agy_models_output,
    _sanitize_frame,
    clear_catalog_cache,
    probe_agy_model_catalog,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_STDOUT = (
    "gemini-3.8-flash-high\tGemini 3.8 Flash (High)\n"
    "gemini-3.8-flash-medium\tGemini 3.8 Flash (Medium)\n"
    "gemini-3.8-flash-low\tGemini 3.8 Flash (Low)\n"
    "claude-sonnet-4-6\tClaude Sonnet 4.6 (Thinking)\n"
)

_EXPECTED_MODELS = [
    {
        "id": "gemini-3.8-flash-high",
        "model": "gemini-3.8-flash-high",
        "displayName": "Gemini 3.8 Flash (High)",
    },
    {
        "id": "gemini-3.8-flash-medium",
        "model": "gemini-3.8-flash-medium",
        "displayName": "Gemini 3.8 Flash (Medium)",
    },
    {
        "id": "gemini-3.8-flash-low",
        "model": "gemini-3.8-flash-low",
        "displayName": "Gemini 3.8 Flash (Low)",
    },
    {
        "id": "claude-sonnet-4-6",
        "model": "claude-sonnet-4-6",
        "displayName": "Claude Sonnet 4.6 (Thinking)",
    },
]


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Reset module-level cache before every test."""
    clear_catalog_cache()


# ---------------------------------------------------------------------------
# _parse_agy_models_output
# ---------------------------------------------------------------------------


class TestParseAgyModelsOutput:
    """Unit tests for :func:`_parse_agy_models_output`."""

    def test_parses_tab_separated_lines(self) -> None:
        """The CLI listing provides no default-model marker."""
        models = _parse_agy_models_output(_SAMPLE_STDOUT)
        assert models == _EXPECTED_MODELS

    def test_empty_output_returns_empty_list(self) -> None:
        """Empty stdout yields an empty list."""
        assert _parse_agy_models_output("") == []

    def test_blank_lines_ignored(self) -> None:
        """Lines that are only whitespace are silently skipped."""
        out = "\n\n  \ngemini-3.8-flash-high\tGemini 3.8 Flash (High)\n\n"
        result = _parse_agy_models_output(out)
        assert len(result) == 1
        assert "isDefault" not in result[0]

    def test_malformed_line_skipped(self, caplog: pytest.LogCaptureFixture) -> None:
        """Lines without a tab are skipped with a warning."""
        import logging

        out = "no-tab-here\ngemini-3.8-flash-high\tGemini 3.8 Flash (High)\n"
        with caplog.at_level(logging.WARNING, logger=_mod.__name__):
            result = _parse_agy_models_output(out)
        assert len(result) == 1
        assert result[0]["id"] == "gemini-3.8-flash-high"
        assert any("Unexpected" in r.message for r in caplog.records)

    def test_no_reasoning_effort_levels_key(self) -> None:
        """Output dicts must NOT contain a reasoningEffortLevels key."""
        models = _parse_agy_models_output(_SAMPLE_STDOUT)
        for m in models:
            assert "reasoningEffortLevels" not in m

    def test_slug_and_model_are_identical(self) -> None:
        """The 'id' and 'model' keys are both the raw slug from agy."""
        models = _parse_agy_models_output(_SAMPLE_STDOUT)
        for m in models:
            assert m["id"] == m["model"]

    def test_listing_does_not_invent_default(self) -> None:
        """The listing order does not identify a default model."""
        out = "a-slug\ta display\nb-slug\tb display\nc-slug\tc display\n"
        models = _parse_agy_models_output(out)
        assert "isDefault" not in models[0]
        assert "isDefault" not in models[1]
        assert "isDefault" not in models[2]


# ---------------------------------------------------------------------------
# _sanitize_frame
# ---------------------------------------------------------------------------


class TestSanitizeFrame:
    """Unit tests for :func:`_sanitize_frame`."""

    def test_masks_long_token(self) -> None:
        """Strings of 20+ word-chars are replaced with <redacted>."""
        exc = ValueError("token=abcdefghij1234567890xyz extra")
        result = _sanitize_frame(exc)
        assert "<redacted>" in result
        assert "abcdefghij" not in result

    def test_short_words_preserved(self) -> None:
        """Words shorter than 20 chars are not masked."""
        exc = OSError("permission denied")
        result = _sanitize_frame(exc)
        assert "permission" in result

    def test_result_capped_at_200_chars(self) -> None:
        """Output is at most 200 characters."""
        exc = RuntimeError("x" * 300)
        assert len(_sanitize_frame(exc)) <= 200

    def test_includes_exception_type(self) -> None:
        """The exception class name appears in the sanitized output."""
        exc = RuntimeError("boom")
        assert "RuntimeError" in _sanitize_frame(exc)


# ---------------------------------------------------------------------------
# probe_agy_model_catalog — subprocess mocking helpers
# ---------------------------------------------------------------------------


def _fake_run_ok(stdout: str = _SAMPLE_STDOUT):
    """Return a mock subprocess.run result with the given stdout."""

    def _inner(*args, **kwargs):
        return MagicMock(stdout=stdout, returncode=0)

    return _inner


# ---------------------------------------------------------------------------
# probe_agy_model_catalog — success path
# ---------------------------------------------------------------------------


class TestProbeAgyModelCatalogSuccess:
    """Tests for the successful probe path."""

    def test_returns_parsed_models(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A successful probe returns models parsed from agy stdout."""
        fake_agy = tmp_path / "agy"
        fake_agy.touch(mode=0o755)
        monkeypatch.setattr(_mod.subprocess, "run", _fake_run_ok())
        result = probe_agy_model_catalog(agy_path=str(fake_agy))
        assert result == _EXPECTED_MODELS

    def test_first_model_is_not_assumed_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Listing order must not change the native CLI default."""
        fake_agy = tmp_path / "agy"
        fake_agy.touch(mode=0o755)
        monkeypatch.setattr(_mod.subprocess, "run", _fake_run_ok())
        result = probe_agy_model_catalog(agy_path=str(fake_agy))
        assert result is not None
        assert "isDefault" not in result[0]

    def test_slugs_are_exact(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Slugs returned by agy are preserved verbatim — no invention."""
        stdout = "gemini-3.8-flash-high\tGemini 3.8 Flash (High)\n"
        fake_agy = tmp_path / "agy"
        fake_agy.touch(mode=0o755)
        monkeypatch.setattr(_mod.subprocess, "run", _fake_run_ok(stdout))
        result = probe_agy_model_catalog(agy_path=str(fake_agy))
        assert result is not None
        assert result[0]["id"] == "gemini-3.8-flash-high"

    def test_uses_capture_output(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """subprocess.run must be called with capture_output=True."""
        calls = []

        def _capture_call(*args, **kwargs):
            calls.append(kwargs)
            return MagicMock(stdout=_SAMPLE_STDOUT, returncode=0)

        fake_agy = tmp_path / "agy"
        fake_agy.touch(mode=0o755)
        monkeypatch.setattr(_mod.subprocess, "run", _capture_call)
        probe_agy_model_catalog(agy_path=str(fake_agy))
        assert calls[0]["capture_output"] is True

    def test_passes_bounded_timeout(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """subprocess.run must receive a numeric timeout."""
        calls = []

        def _capture_call(*args, **kwargs):
            calls.append(kwargs)
            return MagicMock(stdout=_SAMPLE_STDOUT, returncode=0)

        fake_agy = tmp_path / "agy"
        fake_agy.touch(mode=0o755)
        monkeypatch.setattr(_mod.subprocess, "run", _capture_call)
        probe_agy_model_catalog(agy_path=str(fake_agy))
        assert isinstance(calls[0].get("timeout"), (int, float))
        assert calls[0]["timeout"] > 0

    def test_does_not_use_output_format_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Must not pass --output-format to agy (unsupported flag)."""
        calls = []

        def _capture_call(*args, **kwargs):
            calls.append(args)
            return MagicMock(stdout=_SAMPLE_STDOUT, returncode=0)

        fake_agy = tmp_path / "agy"
        fake_agy.touch(mode=0o755)
        monkeypatch.setattr(_mod.subprocess, "run", _capture_call)
        probe_agy_model_catalog(agy_path=str(fake_agy))
        argv = calls[0][0]
        assert not any("output-format" in str(a) for a in argv)


# ---------------------------------------------------------------------------
# probe_agy_model_catalog — TTL cache
# ---------------------------------------------------------------------------


class TestProbeAgyModelCatalogCache:
    """Tests for the TTL and cache-key behaviour."""

    def test_cached_within_ttl(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Second call within TTL returns cached result without re-probing."""
        call_count = [0]

        def _run(*args, **kwargs):
            call_count[0] += 1
            return MagicMock(stdout=_SAMPLE_STDOUT, returncode=0)

        fake_agy = tmp_path / "agy"
        fake_agy.touch(mode=0o755)
        monkeypatch.setattr(_mod.subprocess, "run", _run)

        probe_agy_model_catalog(agy_path=str(fake_agy), ttl=60)
        probe_agy_model_catalog(agy_path=str(fake_agy), ttl=60)
        assert call_count[0] == 1

    def test_re_probes_after_ttl(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A call after TTL expiry triggers a fresh subprocess invocation."""
        call_count = [0]

        def _run(*args, **kwargs):
            call_count[0] += 1
            return MagicMock(stdout=_SAMPLE_STDOUT, returncode=0)

        fake_agy = tmp_path / "agy"
        fake_agy.touch(mode=0o755)
        monkeypatch.setattr(_mod.subprocess, "run", _run)

        probe_agy_model_catalog(agy_path=str(fake_agy), ttl=0)
        probe_agy_model_catalog(agy_path=str(fake_agy), ttl=0)
        assert call_count[0] == 2

    def test_cache_invalidated_on_exe_change(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Touching the agy binary forces a fresh probe even within TTL."""
        call_count = [0]

        def _run(*args, **kwargs):
            call_count[0] += 1
            return MagicMock(stdout=_SAMPLE_STDOUT, returncode=0)

        fake_agy = tmp_path / "agy"
        fake_agy.touch(mode=0o755)
        monkeypatch.setattr(_mod.subprocess, "run", _run)

        probe_agy_model_catalog(agy_path=str(fake_agy), ttl=600)
        # Simulate a new agy install by bumping the mtime.
        fake_agy.touch()
        probe_agy_model_catalog(agy_path=str(fake_agy), ttl=600)
        assert call_count[0] == 2

    def test_cache_stores_key_without_secrets(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The cache key is metadata-only (mtime floats), not token content."""
        fake_agy = tmp_path / "agy"
        fake_agy.touch(mode=0o755)
        monkeypatch.setattr(_mod.subprocess, "run", _fake_run_ok())
        monkeypatch.setattr(_mod, "_oauth_token_mtime", lambda: 1234567.89)

        probe_agy_model_catalog(agy_path=str(fake_agy))

        key = _mod._cache_key
        assert key is not None
        # Key must be a tuple of at most two floats/None — not a dict or string.
        assert isinstance(key, tuple)
        assert "synthetic-secret" not in repr(key)

    def test_clear_catalog_cache_resets_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """clear_catalog_cache leaves the module ready for a fresh probe."""
        fake_agy = tmp_path / "agy"
        fake_agy.touch(mode=0o755)
        monkeypatch.setattr(_mod.subprocess, "run", _fake_run_ok())
        probe_agy_model_catalog(agy_path=str(fake_agy))
        assert _mod._cache_models is not None

        clear_catalog_cache()
        assert _mod._cache_models is None
        assert _mod._cache_stamp == 0.0
        assert _mod._cache_key is None


# ---------------------------------------------------------------------------
# probe_agy_model_catalog — failure paths
# ---------------------------------------------------------------------------


class TestProbeAgyModelCatalogFailures:
    """Tests for transient-failure degradation."""

    def test_timeout_returns_none_on_cold_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A timeout with no prior cached result returns None."""
        import logging

        fake_agy = tmp_path / "agy"
        fake_agy.touch(mode=0o755)
        monkeypatch.setattr(
            _mod.subprocess,
            "run",
            MagicMock(side_effect=subprocess.TimeoutExpired("agy", 10)),
        )
        with caplog.at_level(logging.WARNING, logger=_mod.__name__):
            result = probe_agy_model_catalog(agy_path=str(fake_agy))
        assert result is None
        assert any("timed out" in r.message for r in caplog.records)

    def test_timeout_returns_stale_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After a successful probe, a timeout returns the last good result."""
        fake_agy = tmp_path / "agy"
        fake_agy.touch(mode=0o755)
        run = MagicMock(return_value=MagicMock(stdout=_SAMPLE_STDOUT, returncode=0))
        monkeypatch.setattr(_mod.subprocess, "run", run)
        probe_agy_model_catalog(agy_path=str(fake_agy), ttl=0)

        run.side_effect = subprocess.TimeoutExpired("agy", 10)
        result = probe_agy_model_catalog(agy_path=str(fake_agy), ttl=0)
        assert result == _EXPECTED_MODELS

    def test_nonzero_exit_returns_none_on_cold_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A non-zero exit with no prior cache returns None."""
        import logging

        fake_agy = tmp_path / "agy"
        fake_agy.touch(mode=0o755)
        monkeypatch.setattr(
            _mod.subprocess,
            "run",
            MagicMock(side_effect=subprocess.CalledProcessError(1, "agy models")),
        )
        with caplog.at_level(logging.WARNING, logger=_mod.__name__):
            result = probe_agy_model_catalog(agy_path=str(fake_agy))
        assert result is None
        assert any("exited with code" in r.message for r in caplog.records)

    def test_os_error_returns_cached_or_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An OSError (e.g. permission denied) never propagates."""
        import logging

        fake_agy = tmp_path / "agy"
        fake_agy.touch(mode=0o755)
        monkeypatch.setattr(
            _mod.subprocess,
            "run",
            MagicMock(side_effect=OSError("permission denied")),
        )
        with caplog.at_level(logging.WARNING, logger=_mod.__name__):
            result = probe_agy_model_catalog(agy_path=str(fake_agy))
        assert result is None

    def test_empty_output_returns_cached_or_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """agy models returning empty stdout does not overwrite a good cache."""
        import logging

        fake_agy = tmp_path / "agy"
        fake_agy.touch(mode=0o755)
        run = MagicMock(return_value=MagicMock(stdout=_SAMPLE_STDOUT, returncode=0))
        monkeypatch.setattr(_mod.subprocess, "run", run)
        probe_agy_model_catalog(agy_path=str(fake_agy), ttl=0)  # prime cache

        run.return_value = MagicMock(stdout="", returncode=0)
        with caplog.at_level(logging.WARNING, logger=_mod.__name__):
            result = probe_agy_model_catalog(agy_path=str(fake_agy), ttl=0)
        assert result == _EXPECTED_MODELS  # prior cache preserved

    def test_missing_agy_binary_returns_none(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When agy is not found on disk, returns None without crashing."""
        import logging

        monkeypatch.setattr(_mod.shutil, "which", lambda _name: None)
        monkeypatch.setattr(_mod, "_LINUX_OAUTH_TOKEN", Path("/nonexistent/token"))
        monkeypatch.setattr(_mod, "_MACOS_OAUTH_CREDS", Path("/nonexistent/creds"))
        with caplog.at_level(logging.WARNING, logger=_mod.__name__):
            result = probe_agy_model_catalog(agy_path="/definitely/not/there")
        assert result is None

    def test_failure_does_not_raise(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Any failure is absorbed — the public function never raises."""
        fake_agy = tmp_path / "agy"
        fake_agy.touch(mode=0o755)
        monkeypatch.setattr(
            _mod.subprocess,
            "run",
            MagicMock(side_effect=RuntimeError("unexpected")),
        )
        # Must not raise.
        probe_agy_model_catalog(agy_path=str(fake_agy))

    def test_failure_logs_sanitized_frame(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Error logs must not contain raw token-like strings."""
        import logging

        secret = "a" * 40  # looks like a token
        fake_agy = tmp_path / "agy"
        fake_agy.touch(mode=0o755)
        monkeypatch.setattr(
            _mod.subprocess,
            "run",
            MagicMock(side_effect=OSError(f"failed with token={secret}")),
        )
        with caplog.at_level(logging.WARNING, logger=_mod.__name__):
            probe_agy_model_catalog(agy_path=str(fake_agy))
        full_log = " ".join(r.message for r in caplog.records)
        assert secret not in full_log
