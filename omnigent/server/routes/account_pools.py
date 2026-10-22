"""REST API routes for harness account pools (``/v1/account-pools``).

An account pool groups the subscription accounts that serve one harness
family (Codex, Antigravity, …). These endpoints let the web UI, the mobile
clients, and the CLI manage that inventory and read how each account is
being used. A session create pins an account by id, and the server picks one
by its own ranking when the caller does not.

The inventory is server state, so these routes are admin-scoped: an account
row carries a credential home path, which is host information.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from omnigent.account_pools.labels import ACCOUNT_ID_LABEL_KEY
from omnigent.entities.account_pool import (
    HOME_KIND_CODEX,
    HOME_KIND_GEMINI,
    HOME_KIND_GENERIC,
    AccountPool,
    AccountPoolAccount,
    AccountQuota,
    AccountUsage,
)
from omnigent.errors import ErrorCode, OmnigentError
from omnigent.server.auth import AuthProvider
from omnigent.server.routes._auth_helpers import require_user
from omnigent.stores.account_pool_store import AccountPoolStore

#: How many conversations the usage scan reads per request. The scan is
#: bounded so a large history cannot make a settings page load expensive.
_USAGE_SCAN_LIMIT = 2000

#: Usage periods the API accepts, in days. ``0`` means today.
_USAGE_PERIOD_DAYS = (0, 7, 30)


def _usage_since(days: int) -> int:
    """Return the usage boundary; Today starts at midnight UTC."""
    now = time.time()
    if days:
        return int(now) - days * 86400
    return int(
        datetime.fromtimestamp(now, UTC)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .timestamp()
    )


class CreateAccountPoolRequest(BaseModel):
    """Payload for creating an account pool.

    :param name: Human-readable pool name, unique per workspace.
    :param harness: The harness family the pool serves, e.g.
        ``"codex-native"``.
    :param description: Optional free-text description.
    """

    name: str
    harness: str
    description: str | None = None


class UpdateAccountPoolRequest(BaseModel):
    """Payload for updating an account pool.

    :param name: New pool name, or ``None`` to keep the current one.
    :param harness: New harness family, or ``None`` to keep it.
    :param description: New description, or ``None`` to keep it.
    """

    name: str | None = None
    harness: str | None = None
    description: str | None = None


class CreateAccountRequest(BaseModel):
    """Payload for adding an account to a pool.

    :param id: The account id, unique per workspace.
    :param home_path: Path to the credential home directory.
    :param home_kind: Which credential home the path is — ``"codex_home"``,
        ``"gemini_dir"``, or ``"generic"``.
    :param enabled: Whether the account may be selected for a session.
    """

    id: str
    home_path: str
    home_kind: str = HOME_KIND_GENERIC
    enabled: bool = True


class UpdateAccountRequest(BaseModel):
    """Payload for updating an account.

    :param home_path: New credential home path, or ``None`` to keep it.
    :param enabled: New enabled flag, or ``None`` to keep it.
    """

    home_path: str | None = None
    enabled: bool | None = None


def _quota_response(quota: AccountQuota | None, now: int) -> dict[str, Any] | None:
    """Render a quota snapshot for the API.

    A snapshot older than the freshness window reads as unknown, so the
    client never shows a stale number as if it were current.

    :param quota: The stored snapshot, or ``None``.
    :param now: Unix epoch seconds to measure freshness against.
    :returns: The quota payload, or ``None`` when there is no snapshot.
    """
    if quota is None:
        return None
    fresh = quota.is_fresh(now)
    return {
        "status": quota.status if fresh else "unknown",
        "known": fresh and quota.is_known(),
        "exhausted": quota.is_exhausted(),
        "model_family": quota.model_family,
        "observed_at": quota.observed_at,
        "stale": not fresh,
        "five_hour": {
            "remaining_pct": quota.five_hour.remaining_pct if fresh else None,
            "status": quota.five_hour.status if fresh else "unknown",
            "reset_at": quota.five_hour.reset_at,
        },
        "weekly": {
            "remaining_pct": quota.weekly.remaining_pct if fresh else None,
            "status": quota.weekly.status if fresh else "unknown",
            "reset_at": quota.weekly.reset_at,
        },
    }


def _account_response(
    account: AccountPoolAccount,
    pool: AccountPool | None,
    usage: AccountUsage | None,
    quota: AccountQuota | None,
    now: int,
) -> dict[str, Any]:
    """Render one account for the API.

    :param account: The account to render.
    :param pool: The pool that owns the account, when known.
    :param usage: The account's usage row, when computed.
    :param quota: The account's last known quota.
    :param now: Unix epoch seconds to measure quota freshness against.
    :returns: The account payload.
    """
    return {
        "id": account.id,
        "object": "account",
        "pool_id": account.pool_id,
        "pool_name": pool.name if pool is not None else None,
        "harness": pool.harness if pool is not None else None,
        "home_path": account.home_path,
        "home_kind": account.home_kind,
        "enabled": account.enabled,
        "created_at": account.created_at,
        "updated_at": account.updated_at,
        "quota": _quota_response(quota, now),
        "usage": {
            "active_sessions": usage.active_sessions if usage else 0,
            "sessions": usage.sessions if usage else 0,
            "cost_usd": usage.cost_usd if usage else None,
            "last_used_at": usage.last_used_at if usage else None,
        },
    }


def _pool_response(
    pool: AccountPool,
    usage_by_account: dict[str, AccountUsage],
    quotas: dict[str, AccountQuota],
    now: int,
) -> dict[str, Any]:
    """Render one pool with its accounts for the API.

    :param pool: The pool to render.
    :param usage_by_account: Usage rows keyed by account id.
    :param quotas: Quota snapshots keyed by account id.
    :param now: Unix epoch seconds to measure quota freshness against.
    :returns: The pool payload.
    """
    return {
        "id": pool.id,
        "object": "account_pool",
        "name": pool.name,
        "harness": pool.harness,
        "description": pool.description,
        "created_at": pool.created_at,
        "updated_at": pool.updated_at,
        "accounts": [
            _account_response(
                account,
                pool,
                usage_by_account.get(account.id),
                quotas.get(account.id),
                now,
            )
            for account in pool.accounts
        ],
    }


def _resolve_home_kind(home_kind: str) -> str:
    """Normalize a credential home kind.

    :param home_kind: The requested kind.
    :returns: One of ``"codex_home"``, ``"gemini_dir"``, or ``"generic"``.
    """
    normalized = (home_kind or "").strip().lower()
    if normalized in (HOME_KIND_CODEX, HOME_KIND_GEMINI, HOME_KIND_GENERIC):
        return normalized
    raise OmnigentError(
        f"Unknown home kind {home_kind!r}; expected one of "
        f"{HOME_KIND_CODEX!r}, {HOME_KIND_GEMINI!r}, {HOME_KIND_GENERIC!r}",
        code=ErrorCode.INVALID_INPUT,
    )


def _resolve_home_path(raw_path: str) -> str:
    """Resolve and validate a credential home path.

    :param raw_path: The path as supplied by the caller.
    :returns: The absolute, resolved path.
    :raises OmnigentError: ``INVALID_INPUT`` when the path is empty or is
        not an existing directory.
    """
    if not raw_path or not raw_path.strip():
        raise OmnigentError("Account home path must not be empty", code=ErrorCode.INVALID_INPUT)
    resolved = Path(raw_path).expanduser().resolve()
    if not resolved.is_dir():
        raise OmnigentError(
            f"Account home path {str(resolved)!r} is not an existing directory",
            code=ErrorCode.INVALID_INPUT,
        )
    return str(resolved)


def create_account_pools_router(
    account_pool_store: AccountPoolStore,
    conversation_store: Any | None = None,
    auth_provider: AuthProvider | None = None,
) -> APIRouter:
    """Build the account pool router (``/v1/account-pools``).

    :param account_pool_store: The store backing pool and account
        persistence.
    :param conversation_store: The conversation store, used to compute
        per-account usage.
    :param auth_provider: Auth provider used to identify the requesting user.
        ``None`` in single-user mode.
    :returns: A configured :class:`APIRouter`.
    """
    router = APIRouter()

    def _usage_by_account(since: int) -> dict[str, AccountUsage]:
        """Aggregate per-account session usage from the conversation store.

        :param since: Unix epoch seconds the period starts at.
        :returns: A usage row per account that ran a session.
        """
        if conversation_store is None:
            return {}
        from omnigent.stores.conversation_store import runner_seen_is_fresh

        rows: dict[str, AccountUsage] = {}
        now = int(time.time())
        after = None
        scanned = 0
        while scanned < _USAGE_SCAN_LIMIT:
            page = conversation_store.list_conversations(
                limit=500, after=after, kind=None, include_archived=True
            )
            for conv in page.data:
                scanned += 1
                labels = conv.labels or {}
                account_id = labels.get(ACCOUNT_ID_LABEL_KEY)
                if not account_id:
                    continue
                row = rows.get(account_id)
                if row is None:
                    row = AccountUsage(account_id=account_id, pool_id="", harness="")
                    rows[account_id] = row
                if conv.created_at >= since:
                    row.sessions += 1
                    cost = (conv.session_usage or {}).get("total_cost_usd")
                    if isinstance(cost, (int, float)):
                        row.cost_usd = (row.cost_usd or 0.0) + float(cost)
                    if row.last_used_at is None or conv.created_at > row.last_used_at:
                        row.last_used_at = conv.created_at
                if conv.live_status in ("running", "waiting") and runner_seen_is_fresh(
                    conv.runner_last_seen, now=now
                ):
                    row.active_sessions += 1
            if not page.has_more or page.last_id is None or page.last_id == after:
                break
            after = page.last_id
        return rows

    @router.get("/account-pools")
    async def list_account_pools(
        request: Request,
        period_days: int = 30,
    ) -> dict[str, Any]:
        """List the account pools with their accounts and usage.

        :param request: The incoming request, used to identify the user.
        :param period_days: Usage window in days; ``0`` means today.
        :returns: ``{"object": "list", "data": [...]}``.
        :raises OmnigentError: 401 if unauthenticated in multi-user mode.
        """
        require_user(request, auth_provider)
        days = period_days if period_days in _USAGE_PERIOD_DAYS else 30
        since = _usage_since(days)
        pools = await asyncio.to_thread(account_pool_store.list_pools)
        usage = await asyncio.to_thread(_usage_by_account, since)
        quotas = await asyncio.to_thread(account_pool_store.list_quota_snapshots)
        now = int(time.time())
        for pool in pools:
            for account in pool.accounts:
                row = usage.get(account.id)
                if row is not None:
                    row.pool_id = pool.id
                    row.harness = pool.harness
        return {
            "object": "list",
            "data": [_pool_response(pool, usage, quotas, now) for pool in pools],
        }

    @router.post("/account-pools")
    async def create_account_pool(
        request: Request,
        body: CreateAccountPoolRequest,
    ) -> dict[str, Any]:
        """Create an account pool.

        :param request: The incoming request, used to identify the user.
        :param body: The pool payload.
        :returns: The created pool.
        :raises OmnigentError: 401 if unauthenticated in multi-user mode,
            409 when the pool name is taken.
        """
        require_user(request, auth_provider)
        name = body.name.strip()
        if not name:
            raise OmnigentError("Pool name must not be empty", code=ErrorCode.INVALID_INPUT)
        pool = await asyncio.to_thread(
            account_pool_store.create_pool,
            uuid.uuid4().hex,
            name,
            body.harness.strip(),
            body.description,
        )
        return _pool_response(pool, {}, {}, int(time.time()))

    @router.get("/account-pools/{pool_id}")
    async def get_account_pool(request: Request, pool_id: str) -> dict[str, Any]:
        """Return one account pool with its accounts.

        :param request: The incoming request, used to identify the user.
        :param pool_id: The pool id.
        :returns: The pool.
        :raises OmnigentError: 404 when the pool does not exist.
        """
        require_user(request, auth_provider)
        pool = await asyncio.to_thread(account_pool_store.get_pool, pool_id)
        if pool is None:
            raise OmnigentError(
                f"Account pool {pool_id!r} does not exist",
                code=ErrorCode.NOT_FOUND,
            )
        quotas = await asyncio.to_thread(account_pool_store.list_quota_snapshots)
        return _pool_response(pool, {}, quotas, int(time.time()))

    @router.patch("/account-pools/{pool_id}")
    async def update_account_pool(
        request: Request,
        pool_id: str,
        body: UpdateAccountPoolRequest,
    ) -> dict[str, Any]:
        """Update an account pool.

        :param request: The incoming request, used to identify the user.
        :param pool_id: The pool to update.
        :param body: The fields to change.
        :returns: The updated pool.
        :raises OmnigentError: 404 when the pool does not exist, 409 when the
            new name is taken.
        """
        require_user(request, auth_provider)
        pool = await asyncio.to_thread(
            account_pool_store.update_pool,
            pool_id,
            name=body.name.strip() if body.name else None,
            harness=body.harness.strip() if body.harness else None,
            description=body.description,
        )
        return _pool_response(pool, {}, {}, int(time.time()))

    @router.delete("/account-pools/{pool_id}")
    async def delete_account_pool(request: Request, pool_id: str) -> dict[str, Any]:
        """Delete an account pool and its accounts.

        :param request: The incoming request, used to identify the user.
        :param pool_id: The pool to delete.
        :returns: ``{"id": ..., "object": "account_pool.deleted", "deleted": true}``.
        """
        require_user(request, auth_provider)
        deleted = await asyncio.to_thread(account_pool_store.delete_pool, pool_id)
        return {
            "id": pool_id,
            "object": "account_pool.deleted",
            "deleted": bool(deleted),
        }

    @router.post("/account-pools/{pool_id}/accounts")
    async def create_account(
        request: Request,
        pool_id: str,
        body: CreateAccountRequest,
    ) -> dict[str, Any]:
        """Add an account to a pool.

        :param request: The incoming request, used to identify the user.
        :param pool_id: The pool that owns the account.
        :param body: The account payload.
        :returns: The created account.
        :raises OmnigentError: 404 when the pool does not exist, 409 when the
            account id is taken.
        """
        require_user(request, auth_provider)
        account_id = body.id.strip()
        if not account_id:
            raise OmnigentError("Account id must not be empty", code=ErrorCode.INVALID_INPUT)
        home_kind = _resolve_home_kind(body.home_kind)
        home_path = _resolve_home_path(body.home_path)
        account = await asyncio.to_thread(
            account_pool_store.create_account,
            account_id,
            pool_id,
            home_path,
            home_kind=home_kind,
            enabled=body.enabled,
        )
        pool = await asyncio.to_thread(account_pool_store.get_pool, pool_id)
        return _account_response(account, pool, None, None, int(time.time()))

    @router.get("/accounts")
    async def list_accounts(request: Request, period_days: int = 30) -> dict[str, Any]:
        """List every account with its usage.

        :param request: The incoming request, used to identify the user.
        :param period_days: Usage window in days; ``0`` means today.
        :returns: ``{"object": "list", "data": [...]}``.
        """
        require_user(request, auth_provider)
        days = period_days if period_days in _USAGE_PERIOD_DAYS else 30
        since = _usage_since(days)
        pools = await asyncio.to_thread(account_pool_store.list_pools)
        usage = await asyncio.to_thread(_usage_by_account, since)
        quotas = await asyncio.to_thread(account_pool_store.list_quota_snapshots)
        now = int(time.time())
        data = []
        for pool in pools:
            for account in pool.accounts:
                data.append(
                    _account_response(
                        account,
                        pool,
                        usage.get(account.id),
                        quotas.get(account.id),
                        now,
                    )
                )
        return {"object": "list", "data": data}

    @router.get("/accounts/{account_id}")
    async def get_account(request: Request, account_id: str) -> dict[str, Any]:
        """Return one account.

        :param request: The incoming request, used to identify the user.
        :param account_id: The account id.
        :returns: The account.
        :raises OmnigentError: 404 when the account does not exist.
        """
        require_user(request, auth_provider)
        account = await asyncio.to_thread(account_pool_store.get_account, account_id)
        if account is None:
            raise OmnigentError(
                f"Account {account_id!r} does not exist",
                code=ErrorCode.NOT_FOUND,
            )
        pool = await asyncio.to_thread(account_pool_store.get_pool, account.pool_id)
        quota = await asyncio.to_thread(account_pool_store.get_quota_snapshot, account_id, None)
        return _account_response(account, pool, None, quota, int(time.time()))

    @router.patch("/accounts/{account_id}")
    async def update_account(
        request: Request,
        account_id: str,
        body: UpdateAccountRequest,
    ) -> dict[str, Any]:
        """Update an account.

        :param request: The incoming request, used to identify the user.
        :param account_id: The account to update.
        :param body: The fields to change.
        :returns: The updated account.
        :raises OmnigentError: 404 when the account does not exist.
        """
        require_user(request, auth_provider)
        home_path = _resolve_home_path(body.home_path) if body.home_path else None
        account = await asyncio.to_thread(
            account_pool_store.update_account,
            account_id,
            home_path=home_path,
            enabled=body.enabled,
        )
        pool = await asyncio.to_thread(account_pool_store.get_pool, account.pool_id)
        return _account_response(account, pool, None, None, int(time.time()))

    @router.delete("/accounts/{account_id}")
    async def delete_account(request: Request, account_id: str) -> dict[str, Any]:
        """Delete an account.

        :param request: The incoming request, used to identify the user.
        :param account_id: The account to delete.
        :returns: ``{"id": ..., "object": "account.deleted", "deleted": true}``.
        """
        require_user(request, auth_provider)
        deleted = await asyncio.to_thread(account_pool_store.delete_account, account_id)
        return {"id": account_id, "object": "account.deleted", "deleted": bool(deleted)}

    return router
