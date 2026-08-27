"""ingest_files (снапшот корпуса для инкрементального ingestion)

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-27
"""
from collections.abc import Sequence

import sqlalchemy as sa
import sqlalchemy.dialects.postgresql as pg
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ingest_files",
        sa.Column("filename", sa.String(255), primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("act_ids", pg.JSONB(), nullable=False, server_default="[]"),
        sa.Column("chunks_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("ingest_files")
