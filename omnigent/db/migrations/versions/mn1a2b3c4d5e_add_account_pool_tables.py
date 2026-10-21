"""add account pool tables

Revision ID: mn1a2b3c4d5e
Revises: mm1a2b3c4d5e
Create Date: 2026-10-20 00:00:00.000000

Creates the harness account inventory: ``account_pools`` (a named group of
subscription accounts that serves one harness family), ``account_pool_accounts``
(one account: its credential home path and enabled flag), and
``account_quota_snapshots`` (the last observed quota per account and model
family).

The account inventory used to live in a server YAML block. These tables make
it durable and editable at runtime, so the session-create path, the clients,
and the CLI all read one source of truth. An account row stores a *path* to a
credential home directory, never a token, password, or key.

Deployment: additive; older application code ignores the new tables. Roll
back by downgrading this revision — the account inventory then has to be
supplied by configuration again.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "mn1a2b3c4d5e"
down_revision: str | None = "mm1a2b3c4d5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the account pool, account, and quota snapshot tables."""
    op.create_table(
        "account_pools",
        sa.Column(
            "workspace_id",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("harness", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
        sa.UniqueConstraint("workspace_id", "name", name="uq_account_pools_name"),
    )
    op.create_index(
        "ix_account_pools_harness",
        "account_pools",
        ["workspace_id", "harness", "name"],
    )

    op.create_table(
        "account_pool_accounts",
        sa.Column(
            "workspace_id",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("id", sa.String(128), nullable=False),
        sa.Column("pool_id", sa.String(128), nullable=False),
        sa.Column("home_path", sa.String(1024), nullable=False),
        sa.Column("home_kind", sa.String(32), nullable=False, server_default="generic"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("metadata", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_account_pool_accounts_pool",
        "account_pool_accounts",
        ["workspace_id", "pool_id", "id"],
    )

    op.create_table(
        "account_quota_snapshots",
        sa.Column(
            "workspace_id",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("id", sa.String(256), nullable=False),
        sa.Column("account_id", sa.String(128), nullable=False),
        sa.Column("model_family", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("five_hour_remaining_pct", sa.Float(), nullable=True),
        sa.Column("weekly_remaining_pct", sa.Float(), nullable=True),
        sa.Column("five_hour_reset_at", sa.String(64), nullable=True),
        sa.Column("weekly_reset_at", sa.String(64), nullable=True),
        sa.Column("observed_at", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "id"),
    )
    op.create_index(
        "ix_account_quota_snapshots_account",
        "account_quota_snapshots",
        ["workspace_id", "account_id", "model_family"],
    )


def downgrade() -> None:
    """Drop the account pool, account, and quota snapshot tables."""
    op.drop_index(
        "ix_account_quota_snapshots_account",
        table_name="account_quota_snapshots",
    )
    op.drop_table("account_quota_snapshots")
    op.drop_index(
        "ix_account_pool_accounts_pool",
        table_name="account_pool_accounts",
    )
    op.drop_table("account_pool_accounts")
    op.drop_index("ix_account_pools_harness", table_name="account_pools")
    op.drop_table("account_pools")
