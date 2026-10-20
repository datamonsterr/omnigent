"""Provider quota models and inference-free inspection primitives."""

from __future__ import annotations

import json
import math
import os
import selectors
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omnigent.entities.account_pool import AccountQuota, AccountQuotaWindow

#: The quota window of one period. Alias of the entity class, kept so the
#: probe code and the store speak about the same type.
WindowQuota = AccountQuotaWindow


@dataclass
class QuotaTarget:
    """The credential homes a quota probe may read.

    An account row stores one credential home path; this target spells out
    which provider home it is, so the probe knows which protocol to speak.

    :param id: The account id the quota belongs to.
    :param codex_home: The Codex credential home, or ``None``.
    :param gemini_dir: The Antigravity credential home, or ``None``.
    :param command: Override for the provider CLI, or ``None`` to resolve
        it from ``PATH``.
    :param metadata: Non-secret annotations — ``quota_family`` selects the
        Antigravity model family, ``quota_cache`` an explicit cache path.
    """

    id: str
    codex_home: str | None = None
    gemini_dir: str | None = None
    command: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def parse_codex_rate_limits(result: dict[str, Any]) -> AccountQuota:
    """
    Parse Codex official app-server RPC account/rateLimits/read response without inference.

    Maps primary to five_hour (300 mins) and secondary to weekly (10080 mins).
    """
    quota = AccountQuota(
        five_hour=WindowQuota(status="unknown"),
        weekly=WindowQuota(status="unknown"),
        status="unknown",
        observed_at=int(time.time()),
    )
    if not isinstance(result, dict):
        return quota

    limits = result.get("rateLimits") if isinstance(result.get("rateLimits"), dict) else result
    if not isinstance(limits, dict):
        return quota

    blocked = (
        limits.get("ordinaryUsageAllowed") is False
        or result.get("ordinaryUsageAllowed") is False
        or limits.get("rateLimitReachedType")
        in (
            "rate_limit_reached",
            "workspace_member_credits_depleted",
        )
    )

    for name in ("primary", "secondary"):
        item = limits.get(name)
        if not isinstance(item, dict):
            continue
        duration = item.get("windowDurationMins")
        target_key = "five_hour" if duration == 300 else "weekly" if duration == 10080 else None
        if not target_key and name == "primary":
            target_key = "five_hour"
        elif not target_key and name == "secondary":
            target_key = "weekly"

        used = item.get("usedPercent")
        if (
            isinstance(used, (int, float))
            and not isinstance(used, bool)
            and math.isfinite(used)
            and 0 <= used <= 100
        ):
            remaining = max(0.0, min(100.0, 100.0 - float(used)))
            status = "exhausted" if (blocked or remaining <= 0) else "known"
            target_window = WindowQuota(
                remaining_pct=remaining,
                status=status,
                reset_at=item.get("resetsAt"),
            )
            if target_key == "five_hour":
                quota.five_hour = target_window
            else:
                quota.weekly = target_window

    if blocked:
        quota.status = "exhausted"
    elif quota.five_hour.status == "known" or quota.weekly.status == "known":
        quota.status = "known"
    return quota


def parse_agy_quota(
    data: dict[str, Any], account_id: str | None, family: str | None = "gemini"
) -> AccountQuota:
    """Parse Antigravity cached/probed quota state for an account."""
    quota = AccountQuota(
        five_hour=WindowQuota(status="unknown"),
        weekly=WindowQuota(status="unknown"),
        status="unknown",
        observed_at=int(time.time()),
    )
    if not isinstance(data, dict):
        return quota
    acc_data = (
        data.get(account_id)
        if account_id is not None and isinstance(data.get(account_id), dict)
        else data
    )
    if not isinstance(acc_data, dict):
        return quota

    acc_status = acc_data.get("_status", "unknown")
    if acc_status in ("probe_error", "disabled", "unavailable", "auth_failed"):
        quota.status = acc_status
        return quota

    for period in ("five_hour", "weekly"):
        raw_val = None
        for fam in (family,) if family in ("gemini", "claude") else ():
            k = f"{fam}:{period}"
            if (
                k in acc_data
                and isinstance(acc_data[k], (int, float))
                and not isinstance(acc_data[k], bool)
            ):
                raw_val = float(acc_data[k])
                break
        if raw_val is not None and 0 <= raw_val <= 100:
            status = "exhausted" if raw_val <= 0 else "known"
            win = WindowQuota(remaining_pct=raw_val, status=status)
            if period == "five_hour":
                quota.five_hour = win
            else:
                quota.weekly = win

    if quota.five_hour.status == "known" or quota.weekly.status == "known":
        quota.status = "known"
    elif any(
        token in str(acc_status).lower()
        for token in ("depleted", "limit hit", "blocked", "exhausted")
    ):
        quota.status = "exhausted"
    return quota


def probe_codex_account_limits(
    command_or_path: str,
    timeout_seconds: float = 5.0,
    codex_home: str | None = None,
) -> AccountQuota:
    """
    Read official app-server RPC without inference.

    Executes: <command> app-server --listen stdio://
    Initializes and queries account/rateLimits/read.
    """
    if not shutil.which(command_or_path) and not os.path.exists(command_or_path):
        return AccountQuota(status="unavailable")

    process: subprocess.Popen[bytes] | None = None
    try:
        env = dict(os.environ)
        if codex_home:
            env["CODEX_HOME"] = codex_home
        process = subprocess.Popen(
            [command_or_path, "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
        )

        def send(payload: dict[str, Any]) -> None:
            assert process is not None and process.stdin is not None
            process.stdin.write(json.dumps(payload).encode() + b"\n")
            process.stdin.flush()

        send(
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "omnigent-account-pools", "version": "1.0.0"},
                    "capabilities": {"experimentalApi": True},
                },
            }
        )

        deadline = time.monotonic() + timeout_seconds
        pending = b""
        with selectors.DefaultSelector() as selector:
            assert process.stdout is not None
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                events = selector.select(max(0.0, deadline - time.monotonic()))
                if not events:
                    break
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                pending += chunk
                if len(pending) > 1024 * 1024:
                    return AccountQuota(status="probe_error")
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    try:
                        message = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(message, dict):
                        continue
                    if message.get("id") == 1:
                        if "error" in message:
                            return AccountQuota(status="probe_error")
                        send({"method": "initialized"})
                        send({"id": 2, "method": "account/rateLimits/read"})
                    elif message.get("id") == 2:
                        if "error" in message:
                            return AccountQuota(status="probe_error")
                        return parse_codex_rate_limits(message.get("result", {}))
        return AccountQuota(status="probe_error")
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return AccountQuota(status="probe_error")
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()


def probe_agy_quota(
    gemini_dir: Path | str,
    account_id: str | None = None,
    family: str | None = "gemini",
    cache_path: Path | str | None = None,
    max_age_seconds: float = 120.0,
) -> AccountQuota:
    """Probe Antigravity quota from local cached JSON without inference."""
    base_dir = Path(gemini_dir)
    candidates = (
        [Path(cache_path).expanduser()]
        if cache_path
        else [base_dir / "quota.json", base_dir / "cache" / "quota.json"]
    )
    for candidate in candidates:
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text())
                if not isinstance(data, dict):
                    continue
                observed = data.get("ts", candidate.stat().st_mtime)
                if (
                    not isinstance(observed, (int, float))
                    or not 0 <= time.time() - observed < max_age_seconds
                ):
                    continue
                accounts = data.get("accounts", data)
                if not isinstance(accounts, dict):
                    continue
                quota = parse_agy_quota(accounts, account_id=account_id, family=family)
                quota.observed_at = int(observed)
                return quota
            except (OSError, ValueError, TypeError):
                continue
    return AccountQuota(status="unknown")


_quota_cache: dict[tuple[str, str, str, str], tuple[float, AccountQuota]] = {}


def clear_quota_cache() -> None:
    """Clear memory quota cache (useful for tests)."""
    _quota_cache.clear()


def get_default_quota_provider(
    cache_ttl_seconds: float = 60.0,
) -> Callable[[str, QuotaTarget], AccountQuota]:
    """Return an inference-free cached quota provider function."""

    def _provider(account_id: str, account: QuotaTarget) -> AccountQuota:
        now = time.monotonic()
        cache_key = (
            account_id,
            str(account.codex_home or account.gemini_dir),
            str(account.metadata.get("quota_family")),
            str(account.metadata.get("quota_cache")),
        )
        cached = _quota_cache.get(cache_key)
        if cached is not None:
            ts, quota = cached
            if now - ts < cache_ttl_seconds:
                return quota

        res: AccountQuota | None = None
        if account.codex_home is not None:
            codex_cmd = account.command or shutil.which("codex") or "codex"
            res = probe_codex_account_limits(
                codex_cmd,
                timeout_seconds=2.0,
                codex_home=str(account.codex_home),
            )
        elif account.gemini_dir is not None:
            res = probe_agy_quota(
                account.gemini_dir,
                account_id=account.id,
                family=account.metadata.get("quota_family", "gemini"),
                cache_path=account.metadata.get("quota_cache"),
            )

        if res is None:
            res = AccountQuota(status="unknown")

        _quota_cache[cache_key] = (now, res)
        return res

    return _provider


def quota_target_for(account: Any) -> QuotaTarget:
    """Build the probe target for a stored account.

    :param account: An :class:`~omnigent.entities.account_pool.AccountPoolAccount`.
    :returns: The target describing which credential home to read.
    """
    home_kind = getattr(account, "home_kind", "") or ""
    home_path = getattr(account, "home_path", "") or ""
    metadata = dict(getattr(account, "metadata", {}) or {})
    codex_home = home_path if home_kind == "codex_home" else None
    gemini_dir = home_path if home_kind == "gemini_dir" else None
    return QuotaTarget(
        id=getattr(account, "id", ""),
        codex_home=codex_home,
        gemini_dir=gemini_dir,
        command=metadata.get("command"),
        metadata=metadata,
    )
