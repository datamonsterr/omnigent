"""Provider quota models and inference-free inspection primitives."""

from __future__ import annotations

import json
import math
import os
import selectors
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WindowQuota:
    """Quota window metrics for a period (5h or weekly)."""

    remaining_pct: float | None = None
    status: str = "unknown"  # "known", "unknown", "blocked", "exhausted", "unreported"
    reset_at: float | str | None = None


@dataclass
class AccountQuota:
    """Subscription quota state for an account."""

    five_hour: WindowQuota = field(default_factory=lambda: WindowQuota(status="unknown"))
    weekly: WindowQuota = field(default_factory=lambda: WindowQuota(status="unknown"))
    status: str = (
        "unknown"  # "known", "unknown", "blocked", "exhausted", "probe_error", "auth_failed"
    )
    observed_at: float | None = None

    def is_known(self) -> bool:
        return self.status == "known" and (
            self.five_hour.remaining_pct is not None or self.weekly.remaining_pct is not None
        )

    def is_exhausted(self) -> bool:
        if self.status in ("blocked", "exhausted", "auth_failed"):
            return True
        if self.five_hour.status in ("blocked", "exhausted") or self.weekly.status in (
            "blocked",
            "exhausted",
        ):
            return True
        if self.five_hour.remaining_pct is not None and self.five_hour.remaining_pct <= 0:
            return True
        if self.weekly.remaining_pct is not None and self.weekly.remaining_pct <= 0:
            return True
        return False


def parse_codex_rate_limits(result: dict[str, Any]) -> AccountQuota:
    """
    Parse Codex official app-server RPC account/rateLimits/read response without inference.

    Maps primary to five_hour (300 mins) and secondary to weekly (10080 mins).
    """
    quota = AccountQuota(
        five_hour=WindowQuota(status="unknown"),
        weekly=WindowQuota(status="unknown"),
        status="unknown",
        observed_at=time.time(),
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


def parse_agy_quota(data: dict[str, Any], account_id: str) -> AccountQuota:
    """Parse Antigravity cached/probed quota state for an account."""
    quota = AccountQuota(
        five_hour=WindowQuota(status="unknown"),
        weekly=WindowQuota(status="unknown"),
        status="unknown",
        observed_at=time.time(),
    )
    if not isinstance(data, dict):
        return quota
    acc_data = data.get(account_id) if isinstance(data.get(account_id), dict) else data
    if not isinstance(acc_data, dict):
        return quota

    acc_status = acc_data.get("_status", "unknown")
    if acc_status in ("probe_error", "disabled", "unavailable", "auth_failed"):
        quota.status = acc_status
        return quota

    for period in ("five_hour", "weekly"):
        raw_val = None
        for fam in ("gemini", "claude"):
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
