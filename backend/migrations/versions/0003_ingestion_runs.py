"""ingestion_runs (persisted ingestion state)

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-26
"""
from collections.abc import Sequence

import sqlalchemy as sa
import sqlalchemy.dialects.postgresql as pg
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ingestion_runs",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("state", sa.String(16), nullable=False, server_default="running"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("stage", sa.String(16), nullable=True),
        sa.Column("files_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("files_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chunks_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stats", pg.JSONB(), nullable=True),
    )
    op.create_index("ix_ingestion_runs_started_at", "ingestion_runs", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_ingestion_runs_started_at", table_name="ingestion_runs")
    op.drop_table("ingestion_runs")
