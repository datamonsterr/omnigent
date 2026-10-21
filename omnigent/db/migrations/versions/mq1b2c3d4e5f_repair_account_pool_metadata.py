"""Repair account annotation storage on databases that already ran the pool migration.

Revision ID: mq1b2c3d4e5f
Revises: mp1a2b3c4d5e
Create Date: 2026-10-21 00:00:00.000000

The account-pool migration was deployed with ``metadata`` as TEXT while the
application writes framed binary data. Convert existing TEXT values safely and
leave databases that already have the intended binary column unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
import zstandard
from alembic import op

revision: str = "mq1b2c3d4e5f"
down_revision: str | None = "mp1a2b3c4d5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "account_pool_accounts"
_COLUMN = "metadata"


def _is_binary() -> bool:
    """Return whether the installed account metadata column is already binary."""
    column = next(
        column
        for column in sa.inspect(op.get_bind()).get_columns(_TABLE)
        if column["name"] == _COLUMN
    )
    return isinstance(column["type"], sa.LargeBinary)


def _decode(value: object) -> str:
    """Read both compressed account annotations and legacy plain JSON."""
    if isinstance(value, str):
        if value.startswith("\\x"):
            try:
                value = bytes.fromhex(value[2:])
            except ValueError:
                return value
        else:
            return value
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytearray):
        value = bytes(value)
    if not isinstance(value, bytes):
        raise TypeError(f"expected account metadata bytes, got {type(value).__name__}")
    if not value or value[0] != 0:
        return value.decode("utf-8")
    if len(value) < 2:
        raise ValueError("truncated account metadata frame")
    codec, payload = value[1], value[2:]
    if codec == 1:
        payload = zstandard.ZstdDecompressor().decompress(payload)
    elif codec != 0:
        raise ValueError(f"unknown account metadata codec {codec}")
    return payload.decode("utf-8")


def _alter_type(*, to_binary: bool) -> None:
    """Change the column type while preserving the stored annotation bytes."""
    bind = op.get_bind()
    old_type = sa.Text() if to_binary else sa.LargeBinary()
    new_type = sa.LargeBinary() if to_binary else sa.Text()
    postgresql_using = None
    if bind.dialect.name == "postgresql":
        postgresql_using = (
            "CASE WHEN left(metadata, 2) = (chr(92) || 'x') "
            "THEN decode(substr(metadata, 3), 'hex') "
            "ELSE convert_to(metadata, 'UTF8') END"
            if to_binary
            else "convert_from(metadata, 'UTF8')"
        )
    with op.batch_alter_table(_TABLE) as batch:
        batch.alter_column(
            _COLUMN,
            existing_type=old_type,
            type_=new_type,
            existing_nullable=True,
            postgresql_using=postgresql_using,
        )


def upgrade() -> None:
    """Convert historical TEXT values; keep already-correct deployed columns."""
    if not _is_binary():
        _alter_type(to_binary=True)


def downgrade() -> None:
    """Restore readable JSON text before converting the binary column back."""
    if not _is_binary():
        return
    bind = op.get_bind()
    for workspace_id, account_id, value in bind.execute(
        sa.text(f"SELECT workspace_id, id, {_COLUMN} FROM {_TABLE} WHERE {_COLUMN} IS NOT NULL")
    ).fetchall():
        bind.execute(
            sa.text(
                f"UPDATE {_TABLE} SET {_COLUMN} = :metadata "
                "WHERE workspace_id = :workspace_id AND id = :account_id"
            ),
            {
                "metadata": (
                    _decode(value)
                    if bind.dialect.name == "sqlite"
                    else _decode(value).encode("utf-8")
                ),
                # PostgreSQL/MySQL binary columns need bytes; SQLite should
                # retain text affinity so its rebuilt TEXT column stays text.
                "workspace_id": workspace_id,
                "account_id": account_id,
            },
        )
    _alter_type(to_binary=False)
