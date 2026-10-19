"""Data models for native account pools."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_VALID_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\.]+$")

ACCOUNT_ID_LABEL_KEY = "omnigent.account.id"
ACCOUNT_POOL_LABEL_KEY = "omnigent.account.pool"
CODEX_HOME_LABEL_KEY = "omnigent.codex.codex_home"
GEMINI_DIR_LABEL_KEY = "omnigent.antigravity.gemini_dir"


@dataclass
class AccountConfig:
    """Configuration for an individual account within a pool.

    Credential references only; never stores raw tokens, passwords, or secrets.
    """

    id: str
    codex_home: Path | None = None
    gemini_dir: Path | None = None
    command: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id or not isinstance(self.id, str):
            raise ValueError(f"Account id must be a non-empty string, got {self.id!r}")
        stripped = self.id.strip()
        if not stripped or not _VALID_ID_PATTERN.match(stripped):
            raise ValueError(
                f"Invalid account id {self.id!r}; must be alphanumeric, hyphen, underscore, or dot"
            )
        self.id = stripped

        if self.codex_home is not None:
            if not isinstance(self.codex_home, (str, Path)):
                raise ValueError(
                    f"codex_home must be a string or Path, got {type(self.codex_home)}"
                )
            self.codex_home = Path(self.codex_home).expanduser().resolve()

        if self.gemini_dir is not None:
            if not isinstance(self.gemini_dir, (str, Path)):
                raise ValueError(
                    f"gemini_dir must be a string or Path, got {type(self.gemini_dir)}"
                )
            self.gemini_dir = Path(self.gemini_dir).expanduser().resolve()

    def __repr__(self) -> str:
        # Redact any sensitive dictionary items; credential references only
        return (
            f"AccountConfig(id={self.id!r}, "
            f"codex_home={self.codex_home!r}, "
            f"gemini_dir={self.gemini_dir!r})"
        )


@dataclass
class AccountPool:
    """A named pool of accounts for a specific harness or group."""

    name: str
    accounts: list[AccountConfig] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.name or not isinstance(self.name, str):
            raise ValueError(f"Pool name must be a non-empty string, got {self.name!r}")
        self.name = self.name.strip()
        seen = set()
        for acc in self.accounts:
            if acc.id in seen:
                raise ValueError(f"Duplicate account id {acc.id!r} in pool {self.name!r}")
            seen.add(acc.id)

    def get_account(self, account_id: str) -> AccountConfig | None:
        for acc in self.accounts:
            if acc.id == account_id:
                return acc
        return None


@dataclass
class AccountPoolsConfig:
    """Declarative collection of account pools."""

    pools: dict[str, AccountPool] = field(default_factory=dict)

    def get_pool(self, name: str) -> AccountPool | None:
        return self.pools.get(name)

    def get_pool_for_harness_or_agent(
        self,
        harness: str | None = None,
        agent_name: str | None = None,
    ) -> AccountPool | None:
        candidates: list[str] = []
        if harness:
            candidates.append(harness)
            norm = harness.replace("-native", "").replace("-sdk", "").replace("-ui", "").strip()
            if norm:
                candidates.append(norm)
            if norm in ("antigravity", "agy"):
                candidates.extend(["antigravity", "agy"])
        if agent_name:
            candidates.append(agent_name)
            norm_agent = agent_name.replace("-native", "").replace("-sdk", "").strip()
            if norm_agent:
                candidates.append(norm_agent)
            if norm_agent in ("antigravity", "agy"):
                candidates.extend(["antigravity", "agy"])
        candidates.append("default")

        for key in candidates:
            if key in self.pools:
                return self.pools[key]
        return None

    def find_account(self, account_id: str) -> tuple[AccountConfig, AccountPool] | None:
        for pool in self.pools.values():
            acc = pool.get_account(account_id)
            if acc is not None:
                return acc, pool
        return None


def parse_account_pools(raw: Any) -> AccountPoolsConfig | None:
    """Parse declarative account_pools configuration into AccountPoolsConfig."""
    if not raw or not isinstance(raw, dict):
        return None

    pools: dict[str, AccountPool] = {}
    for pool_name, pool_content in raw.items():
        if not isinstance(pool_name, str) or not pool_name.strip():
            continue
        account_rows: list[Any] = []
        if isinstance(pool_content, list):
            account_rows = pool_content
        elif isinstance(pool_content, dict) and "accounts" in pool_content:
            raw_accs = pool_content.get("accounts")
            if isinstance(raw_accs, list):
                account_rows = raw_accs

        accounts: list[AccountConfig] = []
        for row in account_rows:
            if isinstance(row, dict):
                row_copy = dict(row)
                acc_id = row_copy.pop("id", None) or row_copy.pop("name", None)
                if acc_id:
                    codex_home = row_copy.pop("codex_home", None) or row_copy.pop(
                        "source_codex_home", None
                    )
                    gemini_dir = row_copy.pop("gemini_dir", None) or row_copy.pop(
                        "source_gemini_dir", None
                    )
                    cmd = row_copy.pop("command", None)
                    # Filter out any token/secret keys from metadata
                    safe_meta = {
                        k: v
                        for k, v in row_copy.items()
                        if not any(s in k.lower() for s in ("token", "key", "secret", "password"))
                    }
                    accounts.append(
                        AccountConfig(
                            id=str(acc_id),
                            codex_home=codex_home,
                            gemini_dir=gemini_dir,
                            command=cmd,
                            metadata=safe_meta,
                        )
                    )
            elif isinstance(row, str) and row.strip():
                accounts.append(AccountConfig(id=row.strip()))

        if accounts:
            pools[pool_name.strip()] = AccountPool(name=pool_name.strip(), accounts=accounts)

    return AccountPoolsConfig(pools=pools) if pools else None
