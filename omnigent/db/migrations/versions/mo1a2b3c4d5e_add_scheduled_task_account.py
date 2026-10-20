"""add the account pin to scheduled tasks

Revision ID: mo1a2b3c4d5e
Revises: mn1a2b3c4d5e
Create Date: 2026-10-20 00:00:00.000000

A scheduled task can pin the subscription account its firings run under, so a
recurring job keeps using one credential instead of rotating through the
harness's account pool. The column is nullable: a NULL task keeps the pool
selection.

Deployment: additive; older application code ignores the column. Roll back by
downgrading this revision — pinned tasks then fall back to pool selection.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "mo1a2b3c4d5e"
down_revision: str | None = "mn1a2b3c4d5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "scheduled_tasks"
_COLUMN = "account_id"


def upgrade() -> None:
    """Add the ``account_id`` column to ``scheduled_tasks``."""
    with op.batch_alter_table(_TABLE) as batch:
        batch.add_column(sa.Column(_COLUMN, sa.String(128), nullable=True))


def downgrade() -> None:
    """Drop the ``account_id`` column from ``scheduled_tasks``."""
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_column(_COLUMN)
