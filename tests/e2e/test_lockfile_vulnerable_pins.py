"""Regression guard for vulnerable dependency pins in the repo lockfiles.

A ``trivy repo . --skip-version-check`` scan reported vulnerable dependency
pins across three lockfiles (``pnpm-lock.yaml``, ``uv.lock``,
``web/ios/Gemfile.lock``). Each case below asserts the lockfile no longer
resolves the flagged package inside the vulnerable range the scan
reported, so re-introducing a flagged pin fails this test by finding name.

The rubyzip finding (CVE-2026-85396) is not guarded here: released fastlane
still constrains rubyzip < 3.0.0, and the guard lands with the fastlane
git-ref bump that remediates it.

Pure lockfile checks: no server, LLM, network, or browser required.

Run::

    pytest tests/e2e/test_lockfile_vulnerable_pins.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import tomllib

# Worktree root: tests/e2e/<this file> -> parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _ver(version: str) -> tuple[int, ...]:
    """Dotted-numeric version string -> comparable tuple of ints."""
    return tuple(int(part) for part in re.findall(r"\d+", version))


def _pnpm_versions(name: str) -> set[str]:
    """Every version pnpm-lock.yaml resolves for *name*.

    Matches ``<name>@<version>`` package/snapshot keys (lockfile v9),
    e.g. ``'@tiptap/core@3.30.5':`` or ``react-router@7.18.0(react@...)``.
    The lookbehind rejects a preceding word char, dot, slash, or hyphen so
    e.g. ``react-router`` does not match inside ``preact-router@...`` and
    ``hono`` does not match inside ``@types/hono@...``.
    """
    text = (_REPO_ROOT / "pnpm-lock.yaml").read_text(encoding="utf-8")
    pattern = re.compile(rf"(?<![\w./-]){re.escape(name)}@(\d[\w.\-]*)")
    return set(pattern.findall(text))


def _uv_versions(name: str) -> list[str]:
    """Every version uv.lock resolves for package *name*."""
    data = tomllib.loads((_REPO_ROOT / "uv.lock").read_text(encoding="utf-8"))
    return [pkg.get("version", "0") for pkg in data.get("package", []) if pkg.get("name") == name]


def _gem_versions(name: str) -> set[str]:
    """Every version web/ios/Gemfile.lock resolves for gem *name*.

    Spec lines are exactly four-space indented, ``<name> (<version>)``;
    deeper-indented dependency lines carry ranges and never match.
    """
    text = (_REPO_ROOT / "web" / "ios" / "Gemfile.lock").read_text(encoding="utf-8")
    pattern = re.compile(rf"^    {re.escape(name)} \((\d[\d.]*)\)$", re.MULTILINE)
    return set(pattern.findall(text))


def _assert_no_vulnerable_pin(
    lockfile: str, name: str, versions: set[str] | list[str], fixed: str, advisory: str
) -> None:
    """Fail if any resolved version of *name* is below the fixed version.

    An absent package also passes: removing the dependency remediates the
    finding just as a version bump does.
    """
    vulnerable = sorted(v for v in versions if _ver(v) < _ver(fixed))
    assert not vulnerable, (
        f"{lockfile} still resolves {name} {vulnerable} below the fixed "
        f"version {fixed} for {advisory}"
    )


_PNPM_FINDINGS = [
    ("@tiptap/core", "3.30.5", "GHSA-j95f-988m-3j2f"),
    ("hono", "4.13.11", "CVE-2026-84363 / CVE-2026-84364 / CVE-2026-84365 / CVE-2026-111757"),
    ("js-yaml", "4.3.2", "CVE-2026-84375"),
    ("react-router", "7.18.0", "CVE-2026-53666 / CVE-2026-53669"),
    ("react-router-dom", "6.30.6", "CVE-2026-53668"),
    ("markdown-it", "14.3.3", "CVE-2026-111758 / CVE-2026-111759 / CVE-2026-111766"),
    ("vite", "8.1.6", "CVE-2026-112942 / CVE-2026-112943 / CVE-2026-112944"),
    ("proxy-addr", "2.0.8", "CVE-2026-90711"),
    ("source-map-js", "1.2.2", "CVE-2026-93749"),
    ("tinypool", "2.1.2", "CVE-2026-104848 / CVE-2026-104849"),
    ("fast-uri", "3.1.8", "CVE-2026-84292 / CVE-2026-84394 / CVE-2026-86472"),
    ("ip-address", "10.7.1", "CVE-2026-101910 / CVE-2026-101911 / CVE-2026-101912"),
    ("postcss-selector-parser", "7.1.6", "CVE-2026-104844"),
    ("vitest", "4.1.11", "CVE-2026-84373"),
    ("@vitest/mocker", "4.1.11", "CVE-2026-84373"),
    ("katex", "0.18.2", "CVE-2026-103923"),
    ("dompurify", "3.4.16", "GHSA-p98j-92pf-mc4p / GHSA-6688-9rhm-gjv2"),
    ("http-cache-semantics", "4.3.0", "CVE-2026-93748"),
]


@pytest.mark.parametrize(
    ("name", "fixed", "advisory"),
    _PNPM_FINDINGS,
    ids=[name for name, _, _ in _PNPM_FINDINGS],
)
def test_pnpm_lock_has_no_vulnerable_pin(name: str, fixed: str, advisory: str) -> None:
    _assert_no_vulnerable_pin("pnpm-lock.yaml", name, _pnpm_versions(name), fixed, advisory)


@pytest.mark.parametrize(
    ("name", "major", "fixed"),
    [
        ("undici", 6, "6.28.1"),
        ("undici", 7, "7.29.1"),
        ("brace-expansion", 1, "1.1.21"),
        ("brace-expansion", 2, "2.1.7"),
        ("brace-expansion", 5, "5.0.12"),
    ],
)
def test_pnpm_lock_supported_major_has_no_vulnerable_pin(
    name: str, major: int, fixed: str
) -> None:
    versions = {version for version in _pnpm_versions(name) if _ver(version)[0] == major}
    _assert_no_vulnerable_pin("pnpm-lock.yaml", name, versions, fixed, "Trivy security fixes")


@pytest.mark.parametrize(
    ("name", "fixed", "advisory"),
    [
        ("pillow", "13.0.0", "CVE-2026-112403"),
        ("certifi", "2026.10.19", "GHSA-6q48-6c7w-9gw2"),
        ("aiohttp", "3.14.4", "CVE-2026-111064 / CVE-2026-111066"),
    ],
)
def test_uv_lock_has_no_vulnerable_pin(name: str, fixed: str, advisory: str) -> None:
    _assert_no_vulnerable_pin("uv.lock", name, _uv_versions(name), fixed, advisory)


def test_uv_lock_cryptography_at_fixed_version() -> None:
    _assert_no_vulnerable_pin(
        "uv.lock",
        "cryptography",
        _uv_versions("cryptography"),
        "50.0.0",
        "CVE-2026-69247",
    )


def test_uv_lock_mlflow_outside_ssrf_range() -> None:
    # CVE-2026-71211 (AI Gateway SSRF) flags mlflow <= 3.15.2; 3.16.0 is the
    # first release outside the advisory range. mlflow is transitive (via
    # databricks-mcp in the databricks extra), so absence also passes.
    _assert_no_vulnerable_pin(
        "uv.lock", "mlflow", _uv_versions("mlflow"), "3.16.0", "CVE-2026-71211"
    )


_GEM_FINDINGS = [
    ("aws-sdk-s3", "1.208.0", "CVE-2025-14762"),
    ("excon", "1.5.0", "CVE-2026-54171"),
]


@pytest.mark.parametrize(
    ("name", "fixed", "advisory"),
    _GEM_FINDINGS,
    ids=[name for name, _, _ in _GEM_FINDINGS],
)
def test_ios_gemfile_lock_has_no_vulnerable_pin(name: str, fixed: str, advisory: str) -> None:
    _assert_no_vulnerable_pin("web/ios/Gemfile.lock", name, _gem_versions(name), fixed, advisory)
