"""scan_runs.trace_id: link a run to its OpenTelemetry trace

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("scan_runs", sa.Column("trace_id", sa.Text))


def downgrade() -> None:
    op.drop_column("scan_runs", "trace_id")
